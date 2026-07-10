"""Tests for the startup hook loader (`load_hooks`)."""

from unittest import mock

from django.test import SimpleTestCase

from plugins import loader
from plugins.registry import Registry

# Module-level hooks so the `paths` ("module:function") resolver can import them.
_CALLS: list[str] = []


def _hook_ok() -> None:
    _CALLS.append("ok")


def _hook_boom() -> None:
    raise RuntimeError("boom")


def _fake_ep(name: str, obj: object) -> mock.Mock:
    """A stand-in importlib.metadata EntryPoint: `.name` + `.load()` -> obj."""
    ep = mock.Mock()
    ep.name = name
    ep.load.return_value = obj
    return ep


class LoadHooksPathTests(SimpleTestCase):
    def setUp(self) -> None:
        _CALLS.clear()

    def test_module_function_path_is_invoked(self) -> None:
        loaded = loader.load_hooks(["plugins.tests_loader:_hook_ok"], entry_group="grp", allow=None)
        self.assertEqual(_CALLS, ["ok"])
        self.assertIn("plugins.tests_loader:_hook_ok", loaded)

    def test_bad_paths_are_skipped_not_fatal(self) -> None:
        loaded = loader.load_hooks(
            ["not-a-path", "plugins.tests_loader:missing_attr", "no.such.module:reg"],
            entry_group="grp",
            allow=None,
        )
        self.assertEqual(loaded, [])

    def test_raising_hook_is_skipped_not_fatal(self) -> None:
        loaded = loader.load_hooks(["plugins.tests_loader:_hook_boom"], entry_group="grp", allow=None)
        self.assertEqual(loaded, [])

    def test_module_raising_at_import_is_skipped_not_fatal(self) -> None:
        # import_module runs the module's top-level code; a non-ImportError there
        # (SyntaxError, NameError, a side-effect blowing up) must be caught, not
        # crash boot, so the fork path honors the same guarantee as entry points.
        with mock.patch.object(loader, "import_module", side_effect=RuntimeError("top-level boom")):
            loaded = loader.load_hooks(["some.module:register"], entry_group="grp", allow=None)
        self.assertEqual(loaded, [])


class LoadHooksEntryPointTests(SimpleTestCase):
    def setUp(self) -> None:
        _CALLS.clear()

    def test_entry_points_invoked(self) -> None:
        eps = [_fake_ep("p1", _hook_ok)]
        with mock.patch.object(loader.metadata, "entry_points", return_value=eps) as m:
            loaded = loader.load_hooks([], entry_group="openmagpie.plugins", allow=None)
        m.assert_called_once_with(group="openmagpie.plugins")
        self.assertEqual(_CALLS, ["ok"])
        # Label is symmetric with the env-path form: "group:name".
        self.assertIn("openmagpie.plugins:p1", loaded)

    def test_allow_filters_by_name_and_logs_excluded_at_info(self) -> None:
        eps = [_fake_ep("p1", _hook_ok), _fake_ep("p2", _hook_ok)]
        with (
            mock.patch.object(loader.metadata, "entry_points", return_value=eps),
            self.assertLogs("plugins", level="INFO") as logs,
        ):
            loaded = loader.load_hooks([], entry_group="g", allow=["p2"])
        self.assertEqual(loaded, ["g:p2"])
        self.assertEqual(len(_CALLS), 1)
        # Excluded plugin is noted at INFO (not WARNING: it's routine on a
        # locked-down deployment where every installed plugin is excluded).
        excluded = [r for r in logs.records if "p1" in r.getMessage()]
        self.assertTrue(excluded)
        self.assertEqual(excluded[0].levelname, "INFO")
        self.assertIn("allowlist", excluded[0].getMessage())

    def test_allow_none_loads_all(self) -> None:
        eps = [_fake_ep("p1", _hook_ok), _fake_ep("p2", _hook_ok)]
        with mock.patch.object(loader.metadata, "entry_points", return_value=eps):
            loaded = loader.load_hooks([], entry_group="g", allow=None)
        self.assertEqual(sorted(loaded), ["g:p1", "g:p2"])

    def test_allowlist_entry_matching_nothing_warns(self) -> None:
        eps = [_fake_ep("p1", _hook_ok)]
        with (
            mock.patch.object(loader.metadata, "entry_points", return_value=eps),
            self.assertLogs("plugins", level="WARNING") as logs,
        ):
            loader.load_hooks([], entry_group="g", allow=["p1", "typo"])
        self.assertTrue(any("typo" in line and "no installed plugin" in line for line in logs.output))

    def test_failing_entry_point_load_is_skipped(self) -> None:
        bad = mock.Mock()
        bad.name = "bad"
        bad.load.side_effect = ImportError("nope")
        with mock.patch.object(loader.metadata, "entry_points", return_value=[bad]):
            loaded = loader.load_hooks([], entry_group="g", allow=None)
        self.assertEqual(loaded, [])

    def test_entry_point_group_is_the_public_contract(self) -> None:
        # A fork's pyproject.toml [project.entry-points] must match this exactly.
        self.assertEqual(loader.ENTRY_POINT_GROUP, "openmagpie.plugins")


class HookRegistersIntoRegistryTests(SimpleTestCase):
    def test_hook_populates_a_registry(self) -> None:
        # End-to-end: a hook registers a kind into a Registry, which get() then
        # returns, the same shape the real registries (e.g. watches.actions)
        # use, so a real hook could make an impl dispatchable.
        reg: Registry[str] = Registry("demo")
        ep = _fake_ep("dummy", lambda: reg.register("dummy", "IMPL"))
        with mock.patch.object(loader.metadata, "entry_points", return_value=[ep]):
            loader.load_hooks([], entry_group="g", allow=None)
        self.assertEqual(reg.get("dummy"), "IMPL")
