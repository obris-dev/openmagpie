"""Unit tests for `magpie version`: per-track behind-flagging, CLI-only fix arrow,
server informational + unreachable. Stdlib unittest; run with:
  uv run --package openmagpie python -m unittest discover -s apps/cli/tests
"""

from __future__ import annotations

import unittest
from unittest import mock

import httpx

from openmagpie.commands.version import _latest_product_release, _server_version, version

_MOD = "openmagpie.commands.version"


def _logged(console) -> str:
    return "\n".join(str(c.args[0]) for c in console.log.call_args_list)


def _resp(json_data, status: int = 200):
    """A stand-in httpx.Response: json() returns the payload, raise_for_status is a no-op."""
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = json_data
    r.raise_for_status.return_value = None
    return r


class VersionTests(unittest.TestCase):
    def setUp(self) -> None:
        # version() records the PyPI lookup into the update-check cache; no-op that
        # write in tests so it doesn't touch the real ~/.magpie/config.json.
        p = mock.patch(f"{_MOD}.record")
        p.start()
        self.addCleanup(p.stop)

    @mock.patch(f"{_MOD}._latest_product_release", return_value="0.7.0")
    @mock.patch(f"{_MOD}._server_version", return_value="0.7.0")
    @mock.patch(f"{_MOD}.latest_version", return_value="0.7.0")
    @mock.patch(f"{_MOD}.__version__", "0.7.0")
    @mock.patch(f"{_MOD}.console")
    def test_all_current_no_available(self, console, *_m) -> None:
        version()
        out = _logged(console)
        self.assertIn("0.7.0", out)
        self.assertNotIn("available", out)  # nothing behind -> no parens
        self.assertNotIn("upgrade", out)

    @mock.patch(f"{_MOD}._latest_product_release", return_value="0.7.0")
    @mock.patch(f"{_MOD}._server_version", return_value="0.7.0")
    @mock.patch(f"{_MOD}.latest_version", return_value="0.8.0")
    @mock.patch(f"{_MOD}.__version__", "0.7.0")
    @mock.patch(f"{_MOD}.console")
    def test_cli_behind_shows_available_and_arrow(self, console, *_m) -> None:
        version()
        out = _logged(console)
        self.assertIn("0.8.0 available", out)
        self.assertIn("magpie upgrade", out)  # CLI carries the fix

    @mock.patch(f"{_MOD}._latest_product_release", return_value="0.7.0")
    @mock.patch(f"{_MOD}._server_version", return_value="0.6.0")
    @mock.patch(f"{_MOD}.latest_version", return_value="0.7.0")
    @mock.patch(f"{_MOD}.__version__", "0.7.0")
    @mock.patch(f"{_MOD}.console")
    def test_server_behind_no_fix_arrow(self, console, *_m) -> None:
        version()
        out = _logged(console)
        self.assertIn("0.7.0 available", out)  # server behind -> informational parens
        self.assertNotIn("upgrade", out)  # but NO fix prompt (may be hosted)

    @mock.patch(f"{_MOD}._server_version", return_value=None)
    @mock.patch(f"{_MOD}.latest_version", return_value="0.7.0")
    @mock.patch(f"{_MOD}.__version__", "0.7.0")
    @mock.patch(f"{_MOD}.console")
    def test_server_unreachable(self, console, *_m) -> None:
        version()
        self.assertIn("unreachable", _logged(console))

    @mock.patch(f"{_MOD}._latest_product_release", return_value="0.7.0")
    @mock.patch(f"{_MOD}._server_version", return_value="0.7.0")
    @mock.patch(f"{_MOD}.latest_version", return_value=None)  # PyPI lookup failed (offline / bad shape)
    @mock.patch(f"{_MOD}.__version__", "0.7.0")
    @mock.patch(f"{_MOD}.console")
    def test_cli_latest_offline_is_graceful(self, console, *_m) -> None:
        version()  # must not raise
        out = _logged(console)
        self.assertIn("0.7.0", out)
        self.assertNotIn("available", out)


class ServerVersionTests(unittest.TestCase):
    @mock.patch(f"{_MOD}.app_ctx")
    @mock.patch(f"{_MOD}.httpx.get")
    def test_reads_version_off_a_503_body(self, get, ctx) -> None:
        ctx.return_value.config.server_url = "http://server"
        get.return_value = _resp({"version": "1.2.3"}, status=503)  # degraded but reachable
        self.assertEqual(_server_version(), "1.2.3")
        # honors the TLS-skip seam like every other server call
        self.assertIn("verify", get.call_args.kwargs)

    @mock.patch(f"{_MOD}.app_ctx")
    @mock.patch(f"{_MOD}.httpx.get")
    def test_missing_field_and_odd_bodies_degrade_to_none(self, get, ctx) -> None:
        ctx.return_value.config.server_url = "http://server"
        for payload in ({}, {"version": ""}, [1, 2, 3]):  # missing / empty / non-dict
            get.return_value = _resp(payload)
            self.assertIsNone(_server_version())
        get.return_value = _resp(None)
        get.return_value.json.side_effect = ValueError("not json")  # HTML/error body
        self.assertIsNone(_server_version())

    @mock.patch(f"{_MOD}.app_ctx")
    @mock.patch(f"{_MOD}.httpx.get", side_effect=httpx.ConnectError("down"))
    def test_transport_failure_is_none(self, _get, ctx) -> None:
        ctx.return_value.config.server_url = "http://server"
        self.assertIsNone(_server_version())


class LatestProductReleaseTests(unittest.TestCase):
    @mock.patch(f"{_MOD}.httpx.get")
    def test_semver_max_skipping_prerelease_and_cli_track(self, get) -> None:
        get.return_value = _resp(
            [
                {"tag_name": "v0.2.1"},  # newest by publish date, but not the max
                {"tag_name": "v0.10.0", "prerelease": True},  # skipped: GH prerelease
                {"tag_name": "cli-v9.9.9"},  # skipped: cli-* track (shape)
                {"tag_name": "v0.3.0-rc1"},  # skipped: prerelease (shape)
                {"tag_name": "v0.3.0"},  # the semver-max stable
                {"tag_name": "v0.9.0"},
            ]
        )
        self.assertEqual(_latest_product_release(), "0.9.0")

    @mock.patch(f"{_MOD}.httpx.get", side_effect=httpx.ConnectError("down"))
    def test_lookup_failure_is_none(self, _get) -> None:
        self.assertIsNone(_latest_product_release())


if __name__ == "__main__":
    unittest.main()
