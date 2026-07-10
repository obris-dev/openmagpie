"""Tests for the fail-loud settings guards/parsers and the wired settings."""

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from plugins.guards import resolve_entrypoint_allow, resolve_extra_apps


class ResolveExtraAppsTests(SimpleTestCase):
    def test_clean_list_passes_through(self) -> None:
        self.assertEqual(resolve_extra_apps(["a", "b"], installed=["core"]), ["a", "b"])
        self.assertEqual(resolve_extra_apps([], installed=["core"]), [])

    def test_conflict_with_any_installed_raises(self) -> None:
        # Covers built-in / third-party too, not just LOCAL_APPS.
        with self.assertRaises(ImproperlyConfigured):
            resolve_extra_apps(["rest_framework"], installed=["rest_framework", "core"])

    def test_label_collides_with_dotted_installed_path(self) -> None:
        # `auth` shares a label with `django.contrib.auth`, so it must be caught
        # even though the installed entry is a dotted path.
        with self.assertRaises(ImproperlyConfigured):
            resolve_extra_apps(["auth"], installed=["django.contrib.auth"])

    def test_duplicate_within_list_raises(self) -> None:
        with self.assertRaises(ImproperlyConfigured):
            resolve_extra_apps(["dup", "dup"], installed=["core"])
        # Same label, different paths, still a duplicate.
        with self.assertRaises(ImproperlyConfigured):
            resolve_extra_apps(["a.foo", "b.foo"], installed=["core"])


class ResolveEntrypointAllowTests(SimpleTestCase):
    def test_unset_returns_default(self) -> None:
        self.assertIsNone(resolve_entrypoint_allow(None, default_when_unset=None))  # local: load all
        self.assertEqual(resolve_entrypoint_allow(None, default_when_unset=[]), [])  # cloud: load none

    def test_set_parses_and_strips(self) -> None:
        self.assertEqual(
            resolve_entrypoint_allow(" a , b ,, ", default_when_unset=None),
            ["a", "b"],
        )

    def test_empty_or_whitespace_falls_through_to_default(self) -> None:
        # "" reads as "not really set"; it must not silently disable every plugin.
        self.assertIsNone(resolve_entrypoint_allow("", default_when_unset=None))
        self.assertIsNone(resolve_entrypoint_allow("  ", default_when_unset=None))
        self.assertEqual(resolve_entrypoint_allow("", default_when_unset=[]), [])


class PluginSettingsTests(SimpleTestCase):
    def test_settings_are_well_typed(self) -> None:
        from django.conf import settings

        self.assertIsInstance(settings.PLUGIN_HOOKS, list)
        allow = settings.PLUGIN_ENTRYPOINT_ALLOW
        self.assertTrue(allow is None or isinstance(allow, list))
        self.assertEqual(settings.DATABASE_ROUTERS, ["plugins.db.routers.PluginAppRouter"])
        self.assertIsInstance(settings.PLUGIN_DB_ROUTING, dict)
