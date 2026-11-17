"""`magpie watch action config`: one value changed through the shared preview
and confirm, a secret never taken from argv, a value typed as the one it
replaces. Stdlib `unittest`; run with:
  uv run --package openmagpie python -m unittest discover -s apps/cli/tests
"""

from __future__ import annotations

import unittest
from unittest import mock

import typer

from openmagpie.commands.watch import _action_config
from openmagpie_schema.watch import WatchActionMutationResponse, WatchActionWire, build_watch_action_wire

MASKED_URL = "https://api.example.io/***"
NEW_URL = "https://api.example.io/v1/signals/S/ingest/tok_new"


def _bower() -> WatchActionWire:
    return build_watch_action_wire(
        id="act1",
        kind="bower",
        rank=1,
        config={
            "url": MASKED_URL,
            "columns": {"f_title": "title"},
            "delivery": "instant",
            "digest_interval_seconds": 0,
        },
    )


class ActionConfigTests(unittest.TestCase):
    def _run(
        self, key: str | None, value: str | None = None, *, read: str = "", tty: bool = True, **options: bool
    ) -> mock.Mock:
        current = _bower()
        api = self.api = mock.Mock()
        api.get_action.return_value = current
        api.edit_action.side_effect = lambda *args, dry_run: WatchActionMutationResponse(
            dry_run=dry_run, action=current
        )
        ctx = mock.Mock()
        ctx.api.watch = api
        stdin = mock.Mock()
        stdin.isatty.return_value = tty
        args = {"secret": False, "dry_run": False, "yes": True, **options}
        with (
            mock.patch.object(_action_config, "app_ctx", return_value=ctx),
            mock.patch.object(_action_config, "_read_secret", return_value=read) as reader,
            mock.patch.object(_action_config.sys, "stdin", stdin),
            mock.patch.object(_action_config, "console") as console,
            mock.patch("openmagpie.commands.watch._actions.console"),
            mock.patch("openmagpie.commands.watch._actions._print_action_detail"),
            mock.patch.object(_action_config, "_print_action_detail"),
        ):
            self.reader, self.console = reader, console
            _action_config.action_config.__wrapped__("act1", key, value, **args)
        return api

    def _sent(self) -> dict:
        action_id, kind, config = self.api.edit_action.call_args.args
        self.assertEqual((action_id, kind), ("act1", "bower"))
        return config

    def _refused(self, *args: object, **kwargs: object) -> None:
        with self.assertRaises(typer.Exit):
            self._run(*args, **kwargs)
        self.api.edit_action.assert_not_called()

    def test_a_secret_is_read_hidden_and_only_it_changes(self) -> None:
        self._run("url", read=NEW_URL, secret=True)
        self.reader.assert_called_once()
        config = self._sent()
        # FAILS if the value is not set or the rest of the config is dropped.
        self.assertEqual(config["url"], NEW_URL)
        self.assertEqual(config["columns"], {"f_title": "title"})

    def test_a_secret_given_as_an_argument_is_refused_before_anything(self) -> None:
        # FAILS if --secret takes its value from argv (shell history, ps).
        self._refused("url", NEW_URL, secret=True)
        self.reader.assert_not_called()

    def test_a_masked_key_without_secret_is_refused_before_anything(self) -> None:
        # FAILS if a key the server masks can be set without --secret.
        for value in (NEW_URL, None):
            with self.subTest(value=value):
                self._refused("url", value)
                self.reader.assert_not_called()
        self.assertIn("--secret", self.console.error.call_args.args[0])

    def test_a_secret_is_kept_as_text(self) -> None:
        # A key whose current value is a number, so only --secret keeps it text.
        self._run("digest_interval_seconds", read="300", secret=True)
        # FAILS if a secret that looks like a number is typed as one.
        self.assertEqual(self._sent()["digest_interval_seconds"], "300")

    def test_the_change_goes_through_preview_then_apply(self) -> None:
        self._run("digest_interval_seconds", "300")
        dry_runs = [c.kwargs["dry_run"] for c in self.api.edit_action.call_args_list]
        # FAILS if the change skips the shared preview and confirm.
        self.assertEqual(dry_runs, [True, False])

    def test_a_dry_run_only_previews(self) -> None:
        self._run("digest_interval_seconds", "300", dry_run=True)
        self.assertEqual([c.kwargs["dry_run"] for c in self.api.edit_action.call_args_list], [True])

    def test_a_value_takes_the_type_it_replaces(self) -> None:
        self._run("digest_interval_seconds", "300")
        self.assertEqual(self._sent()["digest_interval_seconds"], 300)
        self._run("columns.f_title", "123")
        # FAILS if a text value that looks like a number becomes one.
        self.assertEqual(self._sent()["columns"]["f_title"], "123")

    def test_a_new_key_in_a_map_of_text_stays_text(self) -> None:
        self._run("columns.f_flag", "true")
        self.assertEqual(self._sent()["columns"], {"f_title": "title", "f_flag": "true"})

    def test_an_object_is_replaced_by_json(self) -> None:
        self._run("columns", '{"c_title": "title"}')
        self.assertEqual(self._sent()["columns"], {"c_title": "title"})

    def test_a_value_of_the_wrong_type_is_refused(self) -> None:
        for key, value in (("digest_interval_seconds", "abc"), ("columns", "title")):
            with self.subTest(key=key):
                self._refused(key, value)

    def test_a_bad_value_is_a_config_error_not_a_version_mismatch(self) -> None:
        with mock.patch.object(_action_config, "_abort_union_validation_error", side_effect=typer.Exit(1)) as abort:
            self._refused("delivery", "weekly")
        # FAILS if the config is not checked before the API call.
        abort.assert_called_once()

    def test_a_bad_path_is_refused_before_reading(self) -> None:
        for key in ("url.host", "columns.", ".x"):
            with self.subTest(key=key):
                self._refused(key)
                self.reader.assert_not_called()

    def test_piped_input_needs_yes_and_is_refused_before_reading(self) -> None:
        self._refused("url", tty=False, yes=False, secret=True)
        self.reader.assert_not_called()
        self._run("url", read=NEW_URL, tty=False, yes=True, secret=True)
        self.assertEqual(self._sent()["url"], NEW_URL)

    def test_no_key_shows_the_config_and_changes_nothing(self) -> None:
        self._run(None)
        shown = self.console.log.call_args.args[0]
        self.assertIn(MASKED_URL, shown)
        self.api.edit_action.assert_not_called()


if __name__ == "__main__":
    unittest.main()
