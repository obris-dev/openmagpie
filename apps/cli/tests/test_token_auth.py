"""Unit tests for the personal-access-token login path.

Stdlib `unittest` (no pytest dependency in the CLI yet). Run with:
  uv run --package openmagpie-cli python -m unittest discover -s apps/cli/tests

These cover the pure logic and the persistence behaviour without a live
server; the full end-to-end path is exercised manually against a running
core (see the PR description).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest import mock

import httpx

from openmagpie.api.auth import AuthApi, AuthUser
from openmagpie.commands.auth import _read_token_secret
from openmagpie.config import Config, UserInfo, load
from openmagpie.constants import PERSONAL_ACCESS_TOKEN_PREFIX, TOKEN_ENV_VAR, is_personal_access_token
from openmagpie.context import AppContext
from openmagpie.http import AuthError, MagpieClient


class ApplyPersonalAccessTokenTests(unittest.TestCase):
    def test_clears_refresh_and_expiry(self) -> None:
        cfg = Config()
        cfg.apply_credentials(access_token="old", refresh_token="r", expires_in=3600)
        cfg.apply_personal_access_token(PERSONAL_ACCESS_TOKEN_PREFIX + "abc")
        self.assertEqual(cfg.access_token, PERSONAL_ACCESS_TOKEN_PREFIX + "abc")
        # No refresh token (PATs don't rotate) and no tracked expiry; this
        # is what makes the http layer send the PAT directly.
        self.assertIsNone(cfg.refresh_token)
        self.assertIsNone(cfg.token_expires_at)


class IsPatTests(unittest.TestCase):
    def test_detects_prefix(self) -> None:
        self.assertTrue(is_personal_access_token(PERSONAL_ACCESS_TOKEN_PREFIX + "x"))
        self.assertFalse(is_personal_access_token("session-token"))
        self.assertFalse(is_personal_access_token(None))


class ReadTokenSecretTests(unittest.TestCase):
    def _piped_stdin(self, line: str) -> mock.MagicMock:
        fake = mock.MagicMock()
        fake.isatty.return_value = False
        fake.readline.return_value = line
        return fake

    def test_reads_piped_stdin_stripped(self) -> None:
        with mock.patch("openmagpie.commands.auth.sys.stdin", self._piped_stdin("  mgp_piped  \n")):
            self.assertEqual(_read_token_secret(), "mgp_piped")

    def test_env_is_not_a_source(self) -> None:
        # MAGPIE_TOKEN is the ambient credential; `login` refuses while it's
        # set, so it's never read here. Stdin wins, env is ignored.
        with (
            mock.patch.dict(os.environ, {TOKEN_ENV_VAR: "mgp_env"}),
            mock.patch("openmagpie.commands.auth.sys.stdin", self._piped_stdin("mgp_piped\n")),
        ):
            self.assertEqual(_read_token_secret(), "mgp_piped")


class SignInWithTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.mkdtemp(prefix="magpie-test-home.")
        self.addCleanup(shutil.rmtree, self._home, ignore_errors=True)
        self._home_patch = mock.patch.dict(os.environ, {"HOME": self._home})
        self._home_patch.start()
        self.addCleanup(self._home_patch.stop)

    def test_persists_pat_with_no_refresh(self) -> None:
        ac = AppContext(server_url="http://localhost:8000")
        self.addCleanup(ac.close)
        fake = AuthUser(id="01ABC", email="x@example.com", account_id="01ACC", created_at="2026-01-01T00:00:00Z")
        with mock.patch.object(ac.api.auth, "me", return_value=fake):
            me = ac.sign_in_with_token(PERSONAL_ACCESS_TOKEN_PREFIX + "token123")

        self.assertEqual(me.email, "x@example.com")
        self.assertEqual(ac.config.access_token, PERSONAL_ACCESS_TOKEN_PREFIX + "token123")
        self.assertIsNone(ac.config.refresh_token)
        self.assertIsNotNone(ac.config.user)
        # Persisted to disk under the throwaway HOME.
        reloaded = load()
        self.assertEqual(reloaded.access_token, PERSONAL_ACCESS_TOKEN_PREFIX + "token123")
        self.assertIsNone(reloaded.refresh_token)

    def test_rejected_token_restores_prior_credentials(self) -> None:
        ac = AppContext(server_url="http://localhost:8000")
        self.addCleanup(ac.close)
        # A prior session login is in place.
        ac.config.apply_credentials(
            access_token="session-tok",
            refresh_token="refresh-tok",
            expires_in=3600,
            user=UserInfo(id="01OLD", email="old@example.com", account_id="01ACC"),
        )
        prior_expiry = ac.config.token_expires_at

        with mock.patch.object(ac.api.auth, "me", side_effect=AuthError(401, {})), self.assertRaises(AuthError):
            ac.sign_in_with_token(PERSONAL_ACCESS_TOKEN_PREFIX + "deadtoken")

        # The staged dead token is rolled back; the prior login is intact,
        # not left half-authenticated.
        self.assertEqual(ac.config.access_token, "session-tok")
        self.assertEqual(ac.config.refresh_token, "refresh-tok")
        self.assertEqual(ac.config.token_expires_at, prior_expiry)
        self.assertIsNotNone(ac.config.user)
        self.assertEqual(ac.config.user.email, "old@example.com")


class AmbientTokenTests(unittest.TestCase):
    """MAGPIE_TOKEN in the environment is an ambient bearer: used per
    request, precedence over the stored login, never refreshed."""

    def _client(self) -> MagpieClient:
        cfg = Config(server_url="http://localhost:8000")
        cfg.apply_credentials(access_token="stored-login", refresh_token="r", expires_in=3600)
        http = MagpieClient(cfg)
        self.addCleanup(http.close)
        return http

    def test_env_token_overrides_stored_login(self) -> None:
        http = self._client()
        with mock.patch.dict(os.environ, {TOKEN_ENV_VAR: "mgp_envtoken"}):
            self.assertEqual(http._bearer_token(), "mgp_envtoken")
            self.assertEqual(http._auth_headers()["Authorization"], "Bearer mgp_envtoken")

    def test_falls_back_to_stored_login_without_env(self) -> None:
        http = self._client()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(TOKEN_ENV_VAR, None)
            self.assertEqual(http._bearer_token(), "stored-login")

    def test_ambient_token_is_not_refreshed(self) -> None:
        http = self._client()
        http._refresh = mock.MagicMock()  # type: ignore[method-assign]
        with mock.patch.dict(os.environ, {TOKEN_ENV_VAR: "mgp_envtoken"}):
            http._ensure_fresh_token()
        http._refresh.assert_not_called()


class DevicePollNoAuthTests(unittest.TestCase):
    """The device-flow poll is a (re-)login op: it must NOT attach the
    stored bearer, or a stale/revoked credential gets the poll itself
    rejected with 401 before the device secret is even checked."""

    def test_poll_sends_no_authorization_header(self) -> None:
        captured: dict[str, httpx.Headers] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = request.headers
            return httpx.Response(200, json={"status": "pending"})

        cfg = Config(server_url="http://localhost:8000")
        cfg.apply_personal_access_token(PERSONAL_ACCESS_TOKEN_PREFIX + "deadtoken")
        http = MagpieClient(cfg)
        self.addCleanup(http.close)
        # Drive the real request-building through a MockTransport (less
        # brittle than stubbing the private client's .get).
        http._client = httpx.Client(base_url=cfg.server_url, transport=httpx.MockTransport(handler))

        AuthApi(http).poll_device_session("sid", device_secret="secret")

        headers = captured["headers"]  # httpx.Headers is case-insensitive
        self.assertNotIn("authorization", headers)
        self.assertEqual(headers["x-device-secret"], "secret")


if __name__ == "__main__":
    unittest.main()
