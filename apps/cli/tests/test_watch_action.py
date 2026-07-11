"""Unit tests for the watch-action editing helpers (no live server).

`_action_recreate_note` (the `watch edit` clobber warning) and
`_run_action_mutation` (the dry-run -> confirm -> apply flow shared by
`watch action add`/`edit`). Stdlib `unittest`; run with:
  uv run --package openmagpie python -m unittest discover -s apps/cli/tests
"""

from __future__ import annotations

import unittest
from unittest import mock

import typer
from pydantic import ValidationError

from openmagpie.commands._shared.errors import _union_error_lines
from openmagpie.commands.watch._actions import (
    _parse_action_or_abort,
    _print_action_detail,
    _run_action_mutation,
)
from openmagpie.commands.watch._crud import _action_edit_seed, _action_recreate_note
from openmagpie_schema.watch import (
    WatchActionInput,
    WatchActionMutationResponse,
    WatchActionWire,
    build_watch_action_input,
    build_watch_action_wire,
)


def _wire(action_id: str) -> WatchActionWire:
    return build_watch_action_wire(id=action_id, kind="log", rank=0, config={})


def _inp(action_id: str = "") -> WatchActionInput:
    return build_watch_action_input(id=action_id, kind="log", config={})


def _resp(action_id: str, *, dry_run: bool) -> WatchActionMutationResponse:
    """The single-action mutation response now NESTS the action node under
    `action`; an empty id models an unpersisted (add dry-run) preview."""
    return WatchActionMutationResponse(dry_run=dry_run, action=_wire(action_id))


class ActionRecreateNoteTests(unittest.TestCase):
    """`watch edit` matches the chain by id; an existing action whose id isn't
    resubmitted is dropped + recreated. The note fires only then."""

    def test_all_ids_resubmitted_no_note(self) -> None:
        note = _action_recreate_note([_wire("a"), _wire("b")], [_inp("a"), _inp("b")])
        self.assertIsNone(note)

    def test_no_current_actions_no_note(self) -> None:
        self.assertIsNone(_action_recreate_note([], [_inp("")]))

    def test_id_less_submission_lists_dropped_actions(self) -> None:
        note = _action_recreate_note([_wire("01AAA"), _wire("01BBB")], [_inp(""), _inp("")])  # neither carries an id
        assert note is not None  # narrows for the asserts below
        self.assertIn("2 existing action", note)  # the count + that they're dropped
        self.assertIn("won't complete", note)  # the consequence, in CLI terms
        self.assertIn("01AAA", note)  # dropped actions listed by id (copy-paste lookup)...
        self.assertIn("01BBB", note)
        self.assertIn("log", note)  # ...alongside their kind
        self.assertIn("magpie watch action edit", note)  # the in-place fix

    def test_partial_drop_lists_only_the_dropped(self) -> None:
        note = _action_recreate_note([_wire("01AAA"), _wire("01BBB")], [_inp("01AAA")])  # 01BBB's id absent -> dropped
        assert note is not None
        self.assertIn("1 existing action", note)  # only one dropped
        self.assertIn("01BBB", note)  # only the dropped one is listed
        self.assertNotIn("01AAA", note)  # the kept one is not


class RunActionMutationTests(unittest.TestCase):
    """The shared add/edit flow: dry-run preview, then `--yes`/confirm gates the
    real apply. `mutate(dry_run)` is called True (preview) then False (apply)."""

    def test_dry_run_stops_before_apply(self) -> None:
        mutate = mock.Mock(return_value=_resp("", dry_run=True))
        result = _run_action_mutation(mutate, is_edit=False, dry_run=True, yes=False)  # add preview -> no id
        self.assertIsNone(result)  # nothing applied
        mutate.assert_called_once_with(True)  # only the preview ran

    def test_yes_applies_without_confirm(self) -> None:
        mutate = mock.Mock(
            side_effect=[
                _resp("01ABC", dry_run=True),  # edit preview keeps id
                _resp("01ABC", dry_run=False),
            ]
        )
        result = _run_action_mutation(mutate, is_edit=True, dry_run=False, yes=True)
        assert result is not None
        self.assertEqual(result.id, "01ABC")
        self.assertEqual(mutate.call_count, 2)  # preview + apply, no typer.confirm

    def test_aborts_when_server_ignores_dry_run(self) -> None:
        # Preview came back NOT flagged dry_run -> the server persisted; abort
        # before the apply call (defense-in-depth, mirrors _run_mutation).
        mutate = mock.Mock(return_value=_resp("01ABC", dry_run=False))
        with self.assertRaises(typer.Exit):
            _run_action_mutation(mutate, is_edit=True, dry_run=False, yes=True)
        mutate.assert_called_once_with(True)  # never reached the apply call

    def test_aborts_when_apply_does_not_persist(self) -> None:
        # Apply came back still dry_run / no id -> nothing persisted; abort.
        mutate = mock.Mock(
            side_effect=[
                _resp("", dry_run=True),  # valid add preview
                _resp("", dry_run=True),  # apply didn't persist
            ]
        )
        with self.assertRaises(typer.Exit):
            _run_action_mutation(mutate, is_edit=False, dry_run=False, yes=True)
        self.assertEqual(mutate.call_count, 2)  # reached apply, then aborted


def _error_text(console_mock: mock.Mock) -> str:
    return " ".join(str(call.args[0]) for call in console_mock.error.call_args_list)


class ParseActionConfigTests(unittest.TestCase):
    """`_parse_action_or_abort` validates the config against the kind's typed shape
    at PARSE time, so a config typo renders per-field errors (like main did) instead
    of surfacing later as a ValidationError the command boundary mislabels as a
    server/CLI version mismatch."""

    def test_valid_config_returns_kind_and_config(self) -> None:
        kind, config = _parse_action_or_abort("kind: log\nconfig: {}\n")
        self.assertEqual(kind, "log")
        self.assertEqual(config, {})

    def test_bad_config_renders_field_errors_not_version_mismatch(self) -> None:
        # semantic_filter needs `instructions` and threshold in (0, 1]; both wrong here.
        text = "kind: semantic_filter\nconfig: {threshold: 5}\n"
        # The per-field printing lives in the shared _abort_union_validation_error, so
        # patch the console it uses (not the caller module's).
        with (
            mock.patch("openmagpie.commands._shared.errors.console") as console_mock,
            self.assertRaises(typer.Exit),
        ):
            _parse_action_or_abort(text)
        messages = _error_text(console_mock)
        self.assertIn("Action config error", messages)  # the parse-time header, not the version line
        # Exact stripped paths (like the plugin sibling pins ["config.1"]): the built-in
        # branch's tagged-union prefix is removed, leaving the operator's own field path.
        self.assertIn("semantic_filter.config.instructions", messages)  # the missing field
        self.assertIn("semantic_filter.config.threshold", messages)  # the out-of-range field
        self.assertNotIn("incompatible versions", messages)  # never the CONTRACT_MISMATCH message
        self.assertNotIn("built-in kind", messages)  # not the plugin fallback's internal contract error
        self.assertNotIn("tagged-union", messages)  # the discriminator-tag prefix is stripped

    def test_unknown_kind_accepted_and_deferred_to_server(self) -> None:
        # The action union is extensible: an unknown (plugin) kind validates as the
        # open plugin member and is accepted here, then gated by the SERVER's
        # known-kinds check (the CLI can't enumerate a deployment's plugin kinds).
        kind, config = _parse_action_or_abort("kind: nope\nconfig: {}\n")
        self.assertEqual(kind, "nope")
        self.assertEqual(config, {})


class NullConfigDegradeTests(unittest.TestCase):
    """A corrupt-at-rest config degrades to `config: null` on the wire; the display
    + edit-seed sites handle the None instead of crashing on it."""

    def test_get_display_renders_null_for_none_config(self) -> None:
        wire = build_watch_action_wire(id="01X", kind="log", rank=0, config=None)
        # Patch only `console.log` (the config-blob sink); the field table uses the
        # real console, and a full-module mock would break `console.EMPTY`.
        with mock.patch("openmagpie.commands.watch._actions.console.log") as log_mock:
            _print_action_detail(wire)  # must not raise AttributeError on the None
        logged = " ".join(str(call.args[0]) for call in log_mock.call_args_list)
        self.assertIn("null", logged)  # the config blob rendered as JSON null

    def test_edit_seed_uses_empty_placeholder_for_none_config(self) -> None:
        wire = build_watch_action_wire(id="01X", kind="log", rank=0, config=None)
        seed = _action_edit_seed(wire)
        # placeholder, not a crash; shape matches the readable branch (kind str, rank key present)
        self.assertEqual(seed, {"id": "01X", "kind": "log", "rank": None, "config": {}})

    def test_edit_seed_round_trips_a_readable_config(self) -> None:
        wire = build_watch_action_wire(id="01Y", kind="log", rank=0, config={"prefix": "[x]"})
        seed = _action_edit_seed(wire)
        self.assertEqual(seed["kind"], "log")
        self.assertEqual(seed["id"], "01Y")
        self.assertEqual(seed["config"]["prefix"], "[x]")


class PluginKindConfigTests(unittest.TestCase):
    """A plugin (non-built-in) kind's `config` is a plain dict on the wire (the
    PluginActionWire fallback member), not a typed model. The display + edit-seed
    sites must normalize it instead of calling `.model_dump()` on a dict (which
    crashed with an uncaught AttributeError, a raw traceback for every add/edit/get
    of a plugin-kind action)."""

    def test_get_display_renders_plugin_kind_dict_config(self) -> None:
        wire = build_watch_action_wire(id="01X", kind="custom_plugin", rank=0, config={"foo": "bar"})
        with mock.patch("openmagpie.commands.watch._actions.console.log") as log_mock:
            _print_action_detail(wire)  # must not raise AttributeError on the dict
        logged = " ".join(str(call.args[0]) for call in log_mock.call_args_list)
        self.assertIn("foo", logged)  # the plugin config blob rendered as JSON

    def test_edit_seed_round_trips_a_plugin_kind_config(self) -> None:
        wire = build_watch_action_wire(id="01Z", kind="custom_plugin", rank=0, config={"foo": "bar"})
        seed = _action_edit_seed(wire)
        self.assertEqual(seed["kind"], "custom_plugin")
        self.assertEqual(seed["config"], {"foo": "bar"})

    def test_action_edit_inline_seed_renders_plugin_config(self) -> None:
        # `watch action edit` with no -f builds the $EDITOR seed inline (a near-twin of
        # _crud._action_edit_seed). Drive that branch for a plugin kind: the seed carries
        # the dict config. Mock the editor to capture the seed and stop before the apply.
        from openmagpie.commands.watch._actions import action_edit

        wire = build_watch_action_wire(id="01P", kind="custom_plugin", rank=0, config={"foo": "bar"})
        captured: dict[str, str] = {}

        def _capture(seed: str) -> str:
            captured["seed"] = seed
            raise typer.Exit(code=0)  # stop before the mutation

        with (
            mock.patch("openmagpie.commands.watch._actions.app_ctx") as app_ctx_mock,
            mock.patch("openmagpie.commands.watch._actions._open_editor_or_abort", side_effect=_capture),
            self.assertRaises(typer.Exit),
        ):
            app_ctx_mock.return_value.api.watch.get_action.return_value = wire
            action_edit("01P", file=None, dry_run=False, yes=False)
        self.assertIn("custom_plugin", captured["seed"])
        self.assertIn("foo", captured["seed"])

    def test_action_edit_inline_seed_renders_builtin_config(self) -> None:
        # Built-in twin of the plugin test above: a built-in kind is a WatchActionKind
        # StrEnum on the wire (not a plain str), and yaml.safe_dump can't represent an
        # enum. Without str(current.kind) this branch crashes with RepresenterError
        # before the editor opens; the plugin-only test can't catch it (plugin kinds
        # are plain str). Drive the inline branch for a built-in kind end to end.
        from openmagpie.commands.watch._actions import action_edit

        wire = build_watch_action_wire(id="01Q", kind="log", rank=0, config={"prefix": "[x]"})
        captured: dict[str, str] = {}

        def _capture(seed: str) -> str:
            captured["seed"] = seed
            raise typer.Exit(code=0)  # stop before the mutation

        with (
            mock.patch("openmagpie.commands.watch._actions.app_ctx") as app_ctx_mock,
            mock.patch("openmagpie.commands.watch._actions._open_editor_or_abort", side_effect=_capture),
            self.assertRaises(typer.Exit),
        ):
            app_ctx_mock.return_value.api.watch.get_action.return_value = wire
            action_edit("01Q", file=None, dry_run=False, yes=False)
        self.assertIn("kind: log", captured["seed"])  # plain scalar, not a !!python/object tag or a crash
        self.assertIn("[x]", captured["seed"])

    def test_plugin_kind_config_errors_drop_builtin_branch(self) -> None:
        # A plugin kind CAN reach the error renderer: a non-string config key (from
        # YAML) fails the open member. Symmetric filtering keeps the plugin member's
        # errors (prefix stripped) and drops the built-in tagged-union branch noise.
        try:
            build_watch_action_input(kind="custom_plugin", config={1: "x"})
            self.fail("expected ValidationError")
        except ValidationError as e:
            paths = [path for path, _ in _union_error_lines(e)]
        # Exact: the plugin member's field error survives with its prefix stripped, and
        # the built-in tagged-union branch is dropped. Asserting the exact path catches a
        # no-op strip if pydantic's fallback-member tag ever drifts from the class name.
        self.assertEqual(paths, ["config.1"])


if __name__ == "__main__":
    unittest.main()
