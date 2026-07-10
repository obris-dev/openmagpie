"""Tests for the generic `Registry[T]` primitive."""

from django.test import SimpleTestCase

from plugins.registry import Registry


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
