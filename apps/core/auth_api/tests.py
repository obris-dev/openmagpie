"""Tests for personal access tokens (`CliToken`): model, service, the
`PersonalAccessTokenAuthentication` path, the `/v1/auth/cli-tokens`
endpoints, and the `issue_cli_token` management command.

The auth-class / endpoint tests deliberately drive requests with a real
`Authorization: Bearer mgp_...` header (not `force_authenticate`) so the
actual resolution path is exercised end to end.
"""

from datetime import datetime, timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Account
from accounts.models.user import User
from accounts.services import AccountService
from auth_api.operations.signup import SignupOperation
from auth_api.services.cli_tokens import TOKEN_PREFIX, CliTokenService
from auth_api.services.tokens import TokenService
from openmagpie_schema.auth import AuthUser

_PASSWORD = "Str0ng-Passw0rd!"


def _make_user(email: str):
    return SignupOperation(email=email, password=_PASSWORD).run()


class SuperuserAccountBindingTests(TestCase):
    """createsuperuser bypasses signup, but every user must belong to an account
    (AuthUser.account_id is non-null; /v1/auth/me + login raise on a user with
    none). So create_superuser REQUIRES an account_id to bind: no silently-minted
    account, and no account-less admin that would 500 on /me."""

    def test_create_superuser_requires_an_account_id(self) -> None:
        with self.assertRaises(ValueError):
            User.objects.create_superuser(email="admin@example.com", password=_PASSWORD)

    def test_create_superuser_rejects_an_unknown_account_id(self) -> None:
        # A wrong/typo'd account_id (not just a missing one) fails loud, rather than
        # binding an owner profile to a nonexistent account.
        with self.assertRaises(Account.DoesNotExist):
            User.objects.create_superuser(
                email="admin-bad@example.com", password=_PASSWORD, account_id="01ARZ3NDEKTSV4RRFFQ69G5FAV"
            )
        self.assertFalse(User.objects.filter(email="admin-bad@example.com").exists())  # atomic: nothing persisted

    def test_superuser_bound_to_the_given_account_has_working_me(self) -> None:
        account = AccountService.Global.create(name="Ops")
        user = User.objects.create_superuser(email="admin2@example.com", password=_PASSWORD, account_id=str(account.id))
        self.assertEqual(AccountService.Global.primary_account_id_for(user_id=str(user.id)), str(account.id))
        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.get("/v1/auth/me")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["account_id"], str(account.id))


class AuthUserContractTests(TestCase):
    """The `{user}` payload the auth endpoints emit is the shared `AuthUser`
    contract (built through `auth_user_wire`), not a hand-mirrored DRF shape, so
    the CLI + web keep parsing it. Pins /v1/auth/me and the signup `{user}` bag to
    AuthUser + its exact wire keys."""

    def test_me_payload_validates_as_auth_user(self) -> None:
        user = _make_user("contract@example.com")
        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.get("/v1/auth/me")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        # Exact wire keys the CLI + web parse verbatim (no extras, none dropped).
        self.assertEqual(set(body), {"id", "email", "account_id", "created_at"})
        model = AuthUser.model_validate(body)  # round-trips through the contract
        self.assertEqual(model.id, str(user.id))
        self.assertEqual(model.email, user.email)
        self.assertTrue(model.account_id)

    def test_signup_user_bag_validates_as_auth_user(self) -> None:
        # Signup mints a token pair, which needs the bootstrapped CLI OAuth app.
        call_command("bootstrap_oauth_app", stdout=StringIO())
        client = APIClient()
        resp = client.post(
            "/v1/auth/signup",
            {"email": "signup-contract@example.com", "password": _PASSWORD},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        AuthUser.model_validate(resp.json()["user"])


class CliTokenModelTests(TestCase):
    def setUp(self) -> None:
        self.user = _make_user("model@example.com")

    def test_active_token_is_valid(self) -> None:
        token, _ = CliTokenService.Global.mint(self.user, name="active")
        self.assertTrue(token.is_valid())

    def test_expired_token_is_invalid(self) -> None:
        token, _ = CliTokenService.Global.mint(self.user, name="expired")
        token.expires_at = timezone.now() - timedelta(seconds=1)
        token.save(update_fields=["expires_at"])
        self.assertFalse(token.is_valid())

    def test_future_expiry_is_valid(self) -> None:
        token, _ = CliTokenService.Global.mint(self.user, name="future", expires_in_days=1)
        self.assertTrue(token.is_valid())
        self.assertIsNotNone(token.expires_at)

    def test_revoked_token_is_invalid(self) -> None:
        token, _ = CliTokenService.Global.mint(self.user, name="revoked")
        token.revoked_at = timezone.now()
        token.save(update_fields=["revoked_at"])
        self.assertFalse(token.is_valid())


class CliTokenServiceTests(TestCase):
    def setUp(self) -> None:
        self.user = _make_user("service@example.com")

    def test_mint_shape_and_storage(self) -> None:
        token, raw = CliTokenService.Global.mint(self.user, name="box")
        self.assertTrue(raw.startswith(TOKEN_PREFIX))
        self.assertEqual(token.last_four, raw[-4:])
        # The raw token is never stored; only its hash.
        self.assertNotEqual(token.token_hash, raw)
        self.assertNotIn(raw, token.token_hash)
        self.assertIsNone(token.expires_at)

    def test_resolve_good_token_returns_user_and_bumps_last_used(self) -> None:
        token, raw = CliTokenService.Global.mint(self.user, name="box")
        self.assertIsNone(token.last_used_at)
        resolved = CliTokenService.Global.resolve(raw)
        self.assertEqual(resolved, self.user)
        token.refresh_from_db()
        self.assertIsNotNone(token.last_used_at)

    def test_resolve_rejects_unknown_revoked_expired_and_nonprefixed(self) -> None:
        self.assertIsNone(CliTokenService.Global.resolve("not-a-magpie-token"))
        self.assertIsNone(CliTokenService.Global.resolve(TOKEN_PREFIX + "deadbeef"))

        revoked_token, revoked_raw = CliTokenService.Global.mint(self.user, name="revoked")
        CliTokenService.Global.revoke(self.user, revoked_token.id)
        self.assertIsNone(CliTokenService.Global.resolve(revoked_raw))

        expired_token, expired_raw = CliTokenService.Global.mint(self.user, name="expired")
        expired_token.expires_at = timezone.now() - timedelta(seconds=1)
        expired_token.save(update_fields=["expires_at"])
        self.assertIsNone(CliTokenService.Global.resolve(expired_raw))

    def test_mint_rejects_blank_name_and_out_of_bounds_expiry(self) -> None:
        # The operator-only management command path isn't serializer-guarded,
        # so mint() enforces the bounds itself.
        with self.assertRaises(ValueError):
            CliTokenService.Global.mint(self.user, name="   ")
        with self.assertRaises(ValueError):
            CliTokenService.Global.mint(self.user, name="ok", expires_in_days=0)
        with self.assertRaises(ValueError):
            CliTokenService.Global.mint(self.user, name="ok", expires_in_days=10_000)

    def test_revoke_is_scoped_to_owner(self) -> None:
        other = _make_user("other@example.com")
        token, raw = CliTokenService.Global.mint(self.user, name="mine")
        # A different user can't revoke it.
        self.assertFalse(CliTokenService.Global.revoke(other, token.id))
        self.assertEqual(CliTokenService.Global.resolve(raw), self.user)
        # The owner can.
        self.assertTrue(CliTokenService.Global.revoke(self.user, token.id))
        self.assertIsNone(CliTokenService.Global.resolve(raw))


class PersonalAccessTokenAuthTests(TestCase):
    # Own `api` attribute (not the base `client`) so it's typed as APIClient
    # and `.credentials(...)` resolves; drives the real Bearer header path.
    def setUp(self) -> None:
        self.user = _make_user("auth@example.com")
        self.api = APIClient()

    def _bearer(self, token: str) -> None:
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_pat_authenticates_me(self) -> None:
        _, raw = CliTokenService.Global.mint(self.user, name="box")
        self._bearer(raw)
        resp = self.api.get("/v1/auth/me")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["email"], "auth@example.com")

    def test_invalid_pat_is_401(self) -> None:
        self._bearer(TOKEN_PREFIX + "bogusbogusbogus")
        self.assertEqual(self.api.get("/v1/auth/me").status_code, 401)

    def test_revoked_pat_is_401(self) -> None:
        token, raw = CliTokenService.Global.mint(self.user, name="box")
        CliTokenService.Global.revoke(self.user, token.id)
        self._bearer(raw)
        self.assertEqual(self.api.get("/v1/auth/me").status_code, 401)

    def test_session_access_token_still_authenticates(self) -> None:
        # A non-PAT bearer must fall through to BearerOrCookieAuthentication.
        call_command("bootstrap_oauth_app", stdout=StringIO())
        access, _refresh, _ttl = TokenService.Global.mint_pair(self.user)
        self._bearer(access.token)
        resp = self.api.get("/v1/auth/me")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["email"], "auth@example.com")


class CliTokenEndpointTests(TestCase):
    def setUp(self) -> None:
        self.user = _make_user("ep@example.com")
        self.api = APIClient()
        # Session tokens (TokenService.Global.mint_pair) need the CLI Application.
        call_command("bootstrap_oauth_app", stdout=StringIO())

    def _session_auth(self, user=None) -> None:
        """Authenticate as a SESSION (browser/device-flow) token, the only
        auth allowed to mint PATs."""
        access, _refresh, _ttl = TokenService.Global.mint_pair(user or self.user)
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {access.token}")

    def _pat_auth(self, user=None) -> None:
        _, raw = CliTokenService.Global.mint(user or self.user, name="session")
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")

    def test_create_returns_raw_token_once(self) -> None:
        self._session_auth()
        resp = self.api.post("/v1/auth/cli-tokens", {"name": "laptop", "expires_in_days": 30}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertTrue(body["token"].startswith(TOKEN_PREFIX))
        self.assertEqual(body["name"], "laptop")
        # Timestamps serialize as parseable, tz-aware ISO-8601 strings.
        created = datetime.fromisoformat(body["created_at"])
        expires = datetime.fromisoformat(body["expires_at"])
        self.assertIsNotNone(created.tzinfo)
        self.assertGreater(expires, created)
        # The minted token actually works.
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {body['token']}")
        self.assertEqual(self.api.get("/v1/auth/me").status_code, 200)

    def test_create_rejected_for_pat_auth(self) -> None:
        # A PAT can't mint another PAT (would survive its own revocation).
        self._pat_auth()
        resp = self.api.post("/v1/auth/cli-tokens", {"name": "child"}, format="json")
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_list_never_leaks_the_secret(self) -> None:
        self._pat_auth()  # listing IS allowed via a PAT
        CliTokenService.Global.mint(self.user, name="listed")
        resp = self.api.get("/v1/auth/cli-tokens")
        self.assertEqual(resp.status_code, 200, resp.content)
        for row in resp.json():
            self.assertNotIn("token", row)
            self.assertNotIn("token_hash", row)
            self.assertIn("last_four", row)

    def test_delete_revokes_and_unknown_is_404(self) -> None:
        self._pat_auth()  # revoking IS allowed via a PAT
        target, _ = CliTokenService.Global.mint(self.user, name="to-delete")
        self.assertEqual(self.api.delete(f"/v1/auth/cli-tokens/{target.id}").status_code, 204)
        target.refresh_from_db()
        self.assertFalse(target.is_valid())
        self.assertEqual(self.api.delete("/v1/auth/cli-tokens/01BOGUS").status_code, 404)

    def test_other_user_cannot_revoke(self) -> None:
        other = _make_user("intruder@example.com")
        victim_token, victim_raw = CliTokenService.Global.mint(self.user, name="victim")
        self._pat_auth(other)
        self.assertEqual(self.api.delete(f"/v1/auth/cli-tokens/{victim_token.id}").status_code, 404)
        self.assertEqual(CliTokenService.Global.resolve(victim_raw), self.user)

    def test_endpoints_require_auth(self) -> None:
        self.assertEqual(self.api.get("/v1/auth/cli-tokens").status_code, 401)


class IssueCliTokenCommandTests(TestCase):
    def setUp(self) -> None:
        self.user = _make_user("cmd@example.com")

    def test_command_mints_and_token_authenticates(self) -> None:
        out = StringIO()
        call_command("issue_cli_token", "--email", "cmd@example.com", "--name", "via-cmd", stdout=out)
        printed = out.getvalue()
        self.assertIn(TOKEN_PREFIX, printed)
        raw = next(word for word in printed.split() if word.startswith(TOKEN_PREFIX))
        self.assertEqual(CliTokenService.Global.resolve(raw), self.user)

    def test_unknown_email_errors(self) -> None:
        with self.assertRaises(CommandError):
            call_command("issue_cli_token", "--email", "nobody@example.com", stdout=StringIO())

    def test_bad_expiry_errors(self) -> None:
        with self.assertRaises(CommandError):
            call_command("issue_cli_token", "--email", "cmd@example.com", "--expires-in-days", "0", stdout=StringIO())
