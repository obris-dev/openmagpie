"""OPENMAGPIE_PLUGIN_API_URLS parse (plugins.guards).

The version-prefix MOUNT + collision logging (conf.urls internals) are tested in
conf/tests_urls.py, next to that code.
"""

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from plugins.guards import resolve_plugin_api_urls


class ResolvePluginApiUrlsTests(SimpleTestCase):
    def test_parses_module_paths(self) -> None:
        self.assertEqual(
            resolve_plugin_api_urls("myfork.urls, other.api.urls"),
            ["myfork.urls", "other.api.urls"],
        )

    def test_unset_or_empty_is_empty(self) -> None:
        self.assertEqual(resolve_plugin_api_urls(None), [])
        self.assertEqual(resolve_plugin_api_urls("  "), [])

    def test_malformed_entries_raise(self) -> None:
        # Not bare dotted module paths: a path, the old prefix= form, an internal
        # space, an embedded tab/newline/`;` (edges are stripped, so these are
        # interior), and an empty segment. Each has a segment that isn't an identifier.
        for bad in (
            "myfork/urls",
            "myfork=myfork.urls",
            "my fork.urls",
            "myfork.urls\timport os",
            "myfork.urls;import os",
            "myfork..urls",
            "import.os",  # `import` is a keyword: a valid identifier lexically, never a module
        ):
            with self.assertRaises(ImproperlyConfigured):
                resolve_plugin_api_urls(bad)

    def test_duplicate_module_raises(self) -> None:
        with self.assertRaises(ImproperlyConfigured):
            resolve_plugin_api_urls("myfork.urls, myfork.urls")
