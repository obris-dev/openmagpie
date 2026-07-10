"""Tests for multi-database routing (`db_routing`, `PluginAppRouter`, `load_db_config`)."""

import json
import os
import tempfile
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from plugins.db import config as db_config
from plugins.db import routing as db_routing
from plugins.db.routers import PluginAppRouter

# A DATABASES map with the aliases the routing tests target, so route_app's
# alias-exists guard passes. The dummy backend never opens a connection.
_ROUTED_DATABASES = {
    "default": {"ENGINE": "django.db.backends.dummy"},
    "alt": {"ENGINE": "django.db.backends.dummy"},
    "rt": {"ENGINE": "django.db.backends.dummy"},
}


def _fake_model(app_label: str) -> mock.Mock:
    """A stand-in Django model class exposing `_meta.app_label`."""
    model = mock.Mock()
    model._meta.app_label = app_label
    return model


@override_settings(DATABASES=_ROUTED_DATABASES)
class DbRoutingTests(SimpleTestCase):
    def setUp(self) -> None:
        db_routing._APP_DB.clear()

    def test_route_and_lookup(self) -> None:
        self.assertIsNone(db_routing.db_for_app("x"))
        db_routing.route_app("x", "alt")
        self.assertEqual(db_routing.db_for_app("x"), "alt")

    def test_route_to_unknown_alias_raises(self) -> None:
        with self.assertRaises(ImproperlyConfigured):
            db_routing.route_app("x", "nope")
        self.assertIsNone(db_routing.db_for_app("x"))  # not recorded on failure


@override_settings(DATABASES=_ROUTED_DATABASES)
class PluginAppRouterTests(SimpleTestCase):
    def setUp(self) -> None:
        db_routing._APP_DB.clear()
        db_routing.route_app("routed", "alt")
        self.router = PluginAppRouter()

    def test_reads_writes_routed_to_alias(self) -> None:
        self.assertEqual(self.router.db_for_read(_fake_model("routed")), "alt")
        self.assertEqual(self.router.db_for_write(_fake_model("routed")), "alt")

    def test_reads_writes_unrouted_use_default(self) -> None:
        self.assertIsNone(self.router.db_for_read(_fake_model("core_app")))
        self.assertIsNone(self.router.db_for_write(_fake_model("core_app")))

    def test_allow_migrate_routed_only_on_its_db(self) -> None:
        self.assertTrue(self.router.allow_migrate("alt", "routed"))
        self.assertFalse(self.router.allow_migrate("default", "routed"))

    def test_allow_migrate_unrouted_only_on_default(self) -> None:
        self.assertTrue(self.router.allow_migrate("default", "core_app"))
        self.assertFalse(self.router.allow_migrate("alt", "core_app"))

    def test_allow_relation_no_opinion(self) -> None:
        self.assertIsNone(self.router.allow_relation(object(), object()))


class DbRoutingConfigTests(SimpleTestCase):
    def setUp(self) -> None:
        db_routing._APP_DB.clear()

    @override_settings(PLUGIN_DB_ROUTING={"cfgapp": "alt"})
    def test_config_routing_used_when_no_runtime(self) -> None:
        self.assertEqual(db_routing.db_for_app("cfgapp"), "alt")

    @override_settings(DATABASES=_ROUTED_DATABASES, PLUGIN_DB_ROUTING={"cfgapp": "alt"})
    def test_runtime_route_wins_over_config(self) -> None:
        db_routing.route_app("cfgapp", "rt")
        self.assertEqual(db_routing.db_for_app("cfgapp"), "rt")


class LoadDbConfigTests(SimpleTestCase):
    def setUp(self) -> None:
        # One auto-cleaned temp dir per test (no leaked mkdtemp dirs).
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())

    def _path(self, name: str = "db.json") -> str:
        return os.path.join(self.tmp, name)

    def _write(self, obj: object) -> str:
        path = self._path()
        with open(path, "w") as fh:
            json.dump(obj, fh)
        return path

    def test_merges_databases_and_returns_routing(self) -> None:
        databases: dict = {"default": {"ENGINE": "django.db.backends.postgresql"}}
        path = self._write({"databases": {"alt": {"NAME": "altdb"}}, "routing": {"app": "alt"}})
        routing = db_config.load_db_config(path, databases, conn_max_age=60)
        self.assertEqual(databases["alt"]["NAME"], "altdb")
        self.assertEqual(databases["alt"]["ENGINE"], "django.db.backends.postgresql")  # inherited from default
        self.assertEqual(databases["alt"]["CONN_MAX_AGE"], 60)
        self.assertEqual(routing, {"app": "alt"})

    def test_inherits_connection_defaults_from_core(self) -> None:
        # A plugin DB that omits USER/HOST/PORT inherits core's resolved values,
        # so the Postgres defaults aren't re-hardcoded (no drift from base.py).
        databases: dict = {"default": {"ENGINE": "e", "USER": "coreuser", "HOST": "coredb", "PORT": "6000"}}
        path = self._write({"databases": {"alt": {"NAME": "altdb"}}})
        db_config.load_db_config(path, databases, conn_max_age=60)
        self.assertEqual(databases["alt"]["USER"], "coreuser")
        self.assertEqual(databases["alt"]["HOST"], "coredb")
        self.assertEqual(databases["alt"]["PORT"], "6000")
        self.assertEqual(databases["alt"]["PASSWORD"], "")  # never inherited

    def test_null_port_becomes_empty_not_the_string_none(self) -> None:
        databases: dict = {"default": {}}
        path = self._write({"databases": {"alt": {"NAME": "altdb", "PORT": None}}})
        db_config.load_db_config(path, databases, conn_max_age=60)
        self.assertEqual(databases["alt"]["PORT"], "")

    def test_missing_or_null_name_raises_improperly_configured(self) -> None:
        for conn in ({"USER": "x"}, {"NAME": None}, {"NAME": ""}):
            path = self._write({"databases": {"alt": conn}})
            with self.assertRaises(ImproperlyConfigured):
                db_config.load_db_config(path, {"default": {}}, conn_max_age=60)

    def test_non_integer_conn_max_age_raises(self) -> None:
        # bool and float rejected too: int(True)/int(1.9) would silently coerce.
        for bad in ("sixty", None, True, 1.9):
            path = self._write({"databases": {"alt": {"NAME": "altdb", "CONN_MAX_AGE": bad}}})
            with self.assertRaises(ImproperlyConfigured):
                db_config.load_db_config(path, {"default": {}}, conn_max_age=60)

    def test_bool_port_is_rejected(self) -> None:
        # bool subclasses int; without an explicit check `true` would become "True".
        path = self._write({"databases": {"alt": {"NAME": "altdb", "PORT": True}}})
        with self.assertRaises(ImproperlyConfigured):
            db_config.load_db_config(path, {"default": {}}, conn_max_age=60)

    def test_non_utf8_file_raises_improperly_configured(self) -> None:
        path = self._path("latin1.json")
        with open(path, "wb") as fh:
            fh.write(b"\xff\xfe not utf-8")
        with self.assertRaises(ImproperlyConfigured):
            db_config.load_db_config(path, {"default": {}}, conn_max_age=60)

    def test_unreadable_path_raises_improperly_configured(self) -> None:
        # A wrong path / unmounted secret must name OPENMAGPIE_DB_CONFIG, not
        # leak a bare FileNotFoundError traceback.
        with self.assertRaises(ImproperlyConfigured):
            db_config.load_db_config(self._path("does-not-exist.json"), {"default": {}}, conn_max_age=60)

    def test_malformed_json_raises_improperly_configured(self) -> None:
        path = self._path("bad.json")
        with open(path, "w") as fh:
            fh.write("{not json")
        with self.assertRaises(ImproperlyConfigured):
            db_config.load_db_config(path, {"default": {}}, conn_max_age=60)

    def test_non_object_top_level_raises_improperly_configured(self) -> None:
        path = self._write([1, 2, 3])
        with self.assertRaises(ImproperlyConfigured):
            db_config.load_db_config(path, {"default": {}}, conn_max_age=60)

    def test_non_object_subshapes_raise_improperly_configured(self) -> None:
        # databases-as-list, connection-as-string, routing-as-string must all
        # fail loud (ImproperlyConfigured), not as a bare AttributeError/ValueError.
        for bad in ({"databases": [1, 2]}, {"databases": {"alt": "x"}}, {"routing": "x"}):
            path = self._write(bad)
            with self.assertRaises(ImproperlyConfigured):
                db_config.load_db_config(path, {"default": {}}, conn_max_age=60)

    def test_conflicting_alias_raises(self) -> None:
        path = self._write({"databases": {"default": {"NAME": "x"}}})
        with self.assertRaises(ImproperlyConfigured):
            db_config.load_db_config(path, {"default": {}}, conn_max_age=60)

    def test_route_to_unknown_alias_raises(self) -> None:
        path = self._write({"routing": {"app": "nope"}})
        with self.assertRaises(ImproperlyConfigured):
            db_config.load_db_config(path, {"default": {}}, conn_max_age=60)
