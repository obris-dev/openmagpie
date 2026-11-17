"""`magpie watch connect-bower`: the key never comes from argv, and the
connect previews, confirms, then applies. Stdlib `unittest`; run with:
  uv run --package openmagpie python -m unittest discover -s apps/cli/tests
"""

from __future__ import annotations

import unittest
from unittest import mock

import typer

from openmagpie.commands.watch import _bower
from openmagpie_schema.watch import BowerConnectColumn, BowerConnectResponse, build_watch_action_wire

COLUMNS = [BowerConnectColumn(label="Title", type="text", field="title")]


def _preview() -> BowerConnectResponse:
    return BowerConnectResponse(dry_run=True, signal_label="Mentions", columns=COLUMNS)


def _created() -> BowerConnectResponse:
    action = build_watch_action_wire(
        id="act1", kind="bower", rank=1, config={"url": "https://api.example.io/***", "fields": {"title": "title"}}
    )
    return BowerConnectResponse(
        dry_run=False, signal_id="sig1", signal_label="Mentions", columns=COLUMNS, action=action
    )


class ReadApiKeyTests(unittest.TestCase):
    def test_the_env_var_wins(self) -> None:
        # FAILS (rather than blocking) if the env var is skipped for stdin.
        stdin = mock.Mock()
        stdin.isatty.return_value = False
        stdin.readline.return_value = "ob_piped\n"
        with (
            mock.patch.dict("os.environ", {"OPENBOWER_API_KEY": " ob_env "}),
            mock.patch.object(_bower.sys, "stdin", stdin),
        ):
            self.assertEqual(_bower._read_api_key(), "ob_env")

    def test_piped_stdin_gives_its_first_line(self) -> None:
        stdin = mock.Mock()
        stdin.isatty.return_value = False
        stdin.readline.return_value = "ob_piped\n"
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(_bower.sys, "stdin", stdin):
            self.assertEqual(_bower._read_api_key(), "ob_piped")

    def test_a_terminal_gets_a_hidden_prompt(self) -> None:
        stdin = mock.Mock()
        stdin.isatty.return_value = True
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch.object(_bower.sys, "stdin", stdin),
            mock.patch.object(_bower.getpass, "getpass", return_value="ob_typed") as prompt,
        ):
            self.assertEqual(_bower._read_api_key(), "ob_typed")
        prompt.assert_called_once()


class ConnectFlowTests(unittest.TestCase):
    def _run(self, **options: object) -> mock.Mock:
        api = mock.Mock()
        api.connect_bower.side_effect = lambda watch_id, body, dry_run=False: _preview() if dry_run else _created()
        args = {"api_url": "https://api.example.io", "label": "", "digest": None, "dry_run": False, "yes": True}
        args.update(options)
        with (
            mock.patch.object(_bower, "app_ctx") as ctx,
            mock.patch.object(_bower, "_read_api_key", return_value="ob_key"),
            mock.patch.object(_bower, "console"),
            mock.patch.object(_bower, "_print_action_detail"),
        ):
            ctx.return_value.api.watch = api
            _bower.connect_bower.__wrapped__("w1", **args)
        return api

    def test_it_previews_then_connects_with_the_key_in_the_body(self) -> None:
        api = self._run()
        self.assertEqual([c.kwargs.get("dry_run", False) for c in api.connect_bower.call_args_list], [True, False])
        body = api.connect_bower.call_args.args[1]
        self.assertEqual(body.api_key, "ob_key")
        self.assertEqual(body.delivery, "instant")

    def test_a_dry_run_stops_after_the_preview(self) -> None:
        api = self._run(dry_run=True)
        self.assertEqual(api.connect_bower.call_count, 1)

    def test_digest_sets_the_cadence(self) -> None:
        body = self._run(digest=300).connect_bower.call_args.args[1]
        self.assertEqual((body.delivery, body.digest_interval_seconds), ("digest", 300))

    def test_piped_input_without_yes_refuses_before_connecting(self) -> None:
        stdin = mock.Mock()
        stdin.isatty.return_value = False
        # FAILS (rather than hanging) if the refusal goes and the confirm prompts.
        with (
            mock.patch.object(_bower.sys, "stdin", stdin),
            mock.patch.object(_bower.typer, "confirm", side_effect=AssertionError("prompted")),
            self.assertRaises(typer.Exit),
        ):
            self._run(yes=False)


if __name__ == "__main__":
    unittest.main()
