"""Tests for the plugin primitive (`Registry`) and startup loader (`load_hooks`)."""

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


class RegistryTests(SimpleTestCase):
    def test_register_get_known_kinds(self) -> None:
        reg: Registry[str] = Registry("demo")
        reg.register("b", "B")
        reg.register("a", "A")
        self.assertEqual(reg.get("a"), "A")
        self.assertTrue(reg.known("a"))
        self.assertFalse(reg.known("z"))
        self.assertEqual(reg.kinds(), ["a", "b"])  # sorted

    def test_get_unknown_raises_keyerror(self) -> None:
        with self.assertRaises(KeyError):
            Registry("demo").get("nope")

    def test_register_replaces_and_returns(self) -> None:
        reg: Registry[str] = Registry("demo")
        self.assertEqual(reg.register("k", "v1"), "v1")
        reg.register("k", "v2")
        self.assertEqual(reg.get("k"), "v2")


class LoadHooksPathTests(SimpleTestCase):
    def setUp(self) -> None:
        _CALLS.clear()

    def test_module_function_path_is_invoked(self) -> None:
        loaded = loader.load_hooks(["plugins.tests:_hook_ok"], entry_group="grp", allow=None)
        self.assertEqual(_CALLS, ["ok"])
        self.assertIn("plugins.tests:_hook_ok", loaded)

    def test_bad_paths_are_skipped_not_fatal(self) -> None:
        loaded = loader.load_hooks(
            ["not-a-path", "plugins.tests:missing_attr", "no.such.module:reg"],
            entry_group="grp",
            allow=None,
        )
        self.assertEqual(loaded, [])

    def test_raising_hook_is_skipped_not_fatal(self) -> None:
        loaded = loader.load_hooks(["plugins.tests:_hook_boom"], entry_group="grp", allow=None)
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
        self.assertIn("p1", loaded)

    def test_allow_filters_by_name(self) -> None:
        eps = [_fake_ep("p1", _hook_ok), _fake_ep("p2", _hook_ok)]
        with mock.patch.object(loader.metadata, "entry_points", return_value=eps):
            loaded = loader.load_hooks([], entry_group="g", allow=["p2"])
        self.assertEqual(loaded, ["p2"])
        self.assertEqual(len(_CALLS), 1)

    def test_allow_none_loads_all(self) -> None:
        eps = [_fake_ep("p1", _hook_ok), _fake_ep("p2", _hook_ok)]
        with mock.patch.object(loader.metadata, "entry_points", return_value=eps):
            loaded = loader.load_hooks([], entry_group="g", allow=None)
        self.assertEqual(sorted(loaded), ["p1", "p2"])

    def test_failing_entry_point_load_is_skipped(self) -> None:
        bad = mock.Mock()
        bad.name = "bad"
        bad.load.side_effect = ImportError("nope")
        with mock.patch.object(loader.metadata, "entry_points", return_value=[bad]):
            loaded = loader.load_hooks([], entry_group="g", allow=None)
        self.assertEqual(loaded, [])


class HookRegistersIntoRegistryTests(SimpleTestCase):
    def test_hook_populates_a_registry(self) -> None:
        # End-to-end: a hook registers a kind into a Registry, which get() then
        # returns -- the same shape the real registries (e.g. watches.actions)
        # use, so a real hook could make an impl dispatchable.
        reg: Registry[str] = Registry("demo")
        ep = _fake_ep("dummy", lambda: reg.register("dummy", "IMPL"))
        with mock.patch.object(loader.metadata, "entry_points", return_value=[ep]):
            loader.load_hooks([], entry_group="g", allow=None)
        self.assertEqual(reg.get("dummy"), "IMPL")


class PluginSettingsTests(SimpleTestCase):
    def test_settings_are_well_typed(self) -> None:
        from django.conf import settings

        self.assertIsInstance(settings.PLUGIN_HOOKS, list)
        allow = settings.PLUGIN_ENTRYPOINT_ALLOW
        self.assertTrue(allow is None or isinstance(allow, list))
