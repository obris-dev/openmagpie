"""Plugin URLconf mounting + collision logging, co-located with `conf/urls.py`.

These exercise conf.urls' own (module-private) `_plugin_api_includes` /
`_log_plugin_routes`, so they live next to that code rather than reaching into it
from another package. The OPENMAGPIE_PLUGIN_API_URLS *parse* (plugins.guards) is
tested in plugins/tests_urls.py.
"""

from types import ModuleType

from django.conf import settings
from django.test import SimpleTestCase, override_settings


class PluginApiUrlIncludeTests(SimpleTestCase):
    # Use a real, existing urlconf module as the stand-in plugin: `include(str)`
    # imports it eagerly, so a fake dotted path can't be used here.
    @override_settings(PLUGIN_API_URLS=["waitlist.urls"])
    def test_plugin_module_is_mounted_under_the_version_prefix(self) -> None:
        from conf.urls import _plugin_api_includes

        includes = _plugin_api_includes()
        self.assertEqual(len(includes), 1)
        # Mounted under /<API_VERSION>/, and the include resolves the fork module.
        self.assertIn(settings.API_VERSION_PREFIX, includes[0].pattern.regex.pattern)
        urlconf = includes[0].urlconf_name
        assert isinstance(urlconf, ModuleType)  # include() of a dotted path resolves the module
        self.assertEqual(urlconf.__name__, "waitlist.urls")

    @override_settings(PLUGIN_API_URLS=[])
    def test_no_plugin_urls_means_no_includes(self) -> None:
        from conf.urls import _plugin_api_includes

        self.assertEqual(_plugin_api_includes(), [])


class PluginRouteCollisionLogTests(SimpleTestCase):
    """Startup logging surfaces /<version>/<segment> collisions (visibility, not a
    throw): a segment that shadows a core route warns; a fresh one is INFO."""

    def _resolver(self, *segments: str):
        from django.urls import include, re_path

        from common.urls import api_path
        from common.views import healthz

        patterns = [api_path(seg, healthz) for seg in segments]
        return re_path(rf"^{settings.API_VERSION_PREFIX}/", include((patterns, "faketest")))

    @override_settings(PLUGIN_API_URLS=["fake.mod"])
    def test_core_collision_warns_fresh_segment_infos(self) -> None:
        from conf.urls import _log_plugin_routes

        # "feeds" is a core segment; "database" is fresh.
        with self.assertLogs("plugins", level="INFO") as logs:
            _log_plugin_routes([self._resolver("feeds", "database")])
        warnings = [r.getMessage() for r in logs.records if r.levelname == "WARNING"]
        infos = [r.getMessage() for r in logs.records if r.levelname == "INFO"]
        self.assertTrue(any("feeds" in m and "core" in m for m in warnings))
        self.assertTrue(any("database" in m for m in infos))

    @override_settings(PLUGIN_API_URLS=["a.mod", "b.mod"])
    def test_second_plugin_claiming_same_segment_warns(self) -> None:
        from conf.urls import _log_plugin_routes

        with self.assertLogs("plugins", level="INFO") as logs:
            _log_plugin_routes([self._resolver("database"), self._resolver("database")])
        warnings = [r.getMessage() for r in logs.records if r.levelname == "WARNING"]
        self.assertTrue(any("already claimed" in m for m in warnings))
