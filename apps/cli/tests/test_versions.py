"""Unit tests for the shared version helpers (openmagpie/versions.py): the numeric
compare key, the is-behind check, and the PyPI latest-version lookup. Stdlib unittest;
run with:
  uv run --package openmagpie python -m unittest discover -s apps/cli/tests
"""

from __future__ import annotations

import unittest
from unittest import mock

import httpx

from openmagpie.versions import as_tuple, is_behind, latest_version

_MOD = "openmagpie.versions"


class AsTupleTests(unittest.TestCase):
    def test_orders_versions(self) -> None:
        self.assertLess(as_tuple("0.7.0"), as_tuple("0.8.0"))
        self.assertLess(as_tuple("0.7.0"), as_tuple("0.7.1"))
        self.assertLess(as_tuple("0.9.0"), as_tuple("0.10.0"))  # numeric, not lexical
        self.assertEqual(as_tuple("0.7.0"), as_tuple("0.7.0"))
        self.assertEqual(as_tuple("0.8.0rc1"), as_tuple("0.8.0"))  # prerelease suffix dropped


class IsBehindTests(unittest.TestCase):
    def test_newer_latest_is_returned(self) -> None:
        self.assertEqual(is_behind("0.7.0", "0.8.0"), "0.8.0")

    def test_same_or_older_latest_is_none(self) -> None:
        self.assertIsNone(is_behind("0.8.0", "0.8.0"))
        self.assertIsNone(is_behind("0.8.0", "0.7.0"))

    def test_unknown_current_or_absent_latest_never_reads_behind(self) -> None:
        self.assertIsNone(is_behind("unknown", "9.9.9"))
        self.assertIsNone(is_behind("", "9.9.9"))
        self.assertIsNone(is_behind("0.7.0", None))


class LatestVersionTests(unittest.TestCase):
    def _resp(self, json_data):
        r = mock.Mock()
        r.raise_for_status.return_value = None
        r.json.return_value = json_data
        return r

    @mock.patch(f"{_MOD}.httpx.get")
    def test_returns_pypi_info_version(self, get) -> None:
        get.return_value = self._resp({"info": {"version": "1.2.3"}})
        self.assertEqual(latest_version(), "1.2.3")

    @mock.patch(f"{_MOD}.httpx.get")
    def test_unexpected_shape_is_none(self, get) -> None:
        get.return_value = self._resp({})  # no info.version -> KeyError -> None
        self.assertIsNone(latest_version())

    @mock.patch(f"{_MOD}.httpx.get", side_effect=httpx.ConnectError("down"))
    def test_offline_is_none(self, _get) -> None:
        self.assertIsNone(latest_version())


if __name__ == "__main__":
    unittest.main()
