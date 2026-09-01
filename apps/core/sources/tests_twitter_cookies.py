"""X/Twitter cookie/proxy resolution tests (the TWITTER_* settings chain).

Split from tests_twitter.py (the connector-behavior tests) to keep each file
focused and under the line cap: this module pins load_cookies' priority order
(inline JSON -> auth_token/ct0 pair -> cookie file -> credentials dir), the
per-file .proxy pin precedence, and the critical-pair / empty-pin guards.
"""

from django.test import SimpleTestCase


class LoadCookiesSettingsTests(SimpleTestCase):
    """The TWITTER_* settings chain: inline JSON beats the pair, the pair
    beats the file, a rotated value applies per call (no import-time cache)."""

    def _settings(self, **overrides):
        from django.test import override_settings

        base = {
            "TWITTER_COOKIES_JSON": "",
            "TWITTER_COOKIE_AUTH_TOKEN": "",
            "TWITTER_COOKIE_CT0": "",
            "TWITTER_COOKIES_FILE": "",
            "TWITTER_CREDENTIALS_DIR": "/nonexistent-for-test",
            "TWITTER_PROXY": "",
        }
        return override_settings(**{**base, **overrides})

    def test_cookies_json_wins(self):
        from sources.connectors.twitter.client import load_cookies

        with self._settings(TWITTER_COOKIES_JSON='{"auth_token": "j", "ct0": "j2"}', TWITTER_COOKIE_AUTH_TOKEN="pair"):
            cookies, proxy = load_cookies()
        self.assertEqual(cookies, {"auth_token": "j", "ct0": "j2"})
        self.assertIsNone(proxy)

    def test_pair_route_and_proxy(self):
        from sources.connectors.twitter.client import load_cookies

        with self._settings(TWITTER_COOKIE_AUTH_TOKEN="a", TWITTER_COOKIE_CT0="c", TWITTER_PROXY="http://p:8080"):
            cookies, proxy = load_cookies()
        self.assertEqual(cookies, {"auth_token": "a", "ct0": "c"})
        self.assertEqual(proxy, "http://p:8080")

    def test_nothing_configured_is_guest_mode(self):
        from sources.connectors.twitter.client import load_cookies

        with self._settings():
            cookies, proxy = load_cookies()
        self.assertEqual(cookies, {})
        self.assertIsNone(proxy)

    def test_pair_beats_file(self):
        import json
        import tempfile
        from pathlib import Path

        from sources.connectors.twitter.client import load_cookies

        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "cookies.json"
            f.write_text(json.dumps({"auth_token": "file", "ct0": "file2"}), encoding="utf-8")
            with self._settings(
                TWITTER_COOKIE_AUTH_TOKEN="pair", TWITTER_COOKIE_CT0="pair2", TWITTER_COOKIES_FILE=str(f)
            ):
                cookies, _ = load_cookies()
        self.assertEqual(cookies, {"auth_token": "pair", "ct0": "pair2"})

    def test_file_route_when_no_pair(self):
        import json
        import tempfile
        from pathlib import Path

        from sources.connectors.twitter.client import load_cookies

        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "cookies.json"
            f.write_text(json.dumps({"auth_token": "file", "ct0": "file2"}), encoding="utf-8")
            with self._settings(TWITTER_COOKIES_FILE=str(f)):
                cookies, _ = load_cookies()
        self.assertEqual(cookies, {"auth_token": "file", "ct0": "file2"})

    def test_credentials_dir_with_proxy_pin(self):
        import json
        import tempfile
        from pathlib import Path

        from sources.connectors.twitter.client import load_cookies

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "acct.json").write_text(json.dumps({"auth_token": "a", "ct0": "c"}), encoding="utf-8")
            (Path(d) / "acct.proxy").write_text("http://pin:9090\n", encoding="utf-8")
            with self._settings(TWITTER_CREDENTIALS_DIR=d, TWITTER_PROXY="http://global:1"):
                cookies, proxy = load_cookies()
        self.assertEqual(cookies, {"auth_token": "a", "ct0": "c"})
        self.assertEqual(proxy, "http://pin:9090")  # per-file pin overrides the global

    def test_empty_proxy_pin_falls_back_to_global(self):
        import json
        import tempfile
        from pathlib import Path

        from sources.connectors.twitter.client import load_cookies

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "acct.json").write_text(json.dumps({"auth_token": "a", "ct0": "c"}), encoding="utf-8")
            (Path(d) / "acct.proxy").write_text("  \n", encoding="utf-8")  # empty after strip
            with self._settings(TWITTER_CREDENTIALS_DIR=d, TWITTER_PROXY="http://global:1"):
                _, proxy = load_cookies()
        self.assertEqual(proxy, "http://global:1")  # empty pin must not shadow the global with ""

    def test_json_missing_pair_falls_through(self):
        from sources.connectors.twitter.client import load_cookies

        # A JSON dict without the critical pair must not return a broken session;
        # it falls through to the pair route (here, guest mode).
        with self._settings(TWITTER_COOKIES_JSON='{"guest_id": "x"}'):
            cookies, _ = load_cookies()
        self.assertEqual(cookies, {})
