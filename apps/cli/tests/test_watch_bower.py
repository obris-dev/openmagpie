"""`magpie watch connect-bower`: the key never comes from argv, and the
connect previews, confirms, then applies. Stdlib `unittest`; run with:
  uv run --package openmagpie python -m unittest discover -s apps/cli/tests
"""

from __future__ import annotations

import unittest
from unittest import mock

import typer
from typer.testing import CliRunner

from openmagpie.cli import app
from openmagpie.commands._shared import secrets
from openmagpie.commands.watch import _bower
from openmagpie_schema.watch import (
    BowerConnectColumn,
    BowerConnectExisting,
    BowerConnectNew,
    BowerConnectResponse,
    build_watch_action_wire,
)

COLUMNS = [BowerConnectColumn(label="Title", type="text", field="title")]


def _preview() -> BowerConnectResponse:
    return BowerConnectResponse(dry_run=True, signal_label="Mentions", columns=COLUMNS)


def _created() -> BowerConnectResponse:
    action = build_watch_action_wire(
        id="act1", kind="bower", rank=1, config={"url": "https://api.example.io/***", "columns": {"title": "title"}}
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
            mock.patch.object(secrets.sys, "stdin", stdin),
        ):
            self.assertEqual(_bower._read_api_key(), "ob_env")

    def test_piped_stdin_gives_its_first_line(self) -> None:
        stdin = mock.Mock()
        stdin.isatty.return_value = False
        stdin.readline.return_value = "ob_piped\n"
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(secrets.sys, "stdin", stdin):
            self.assertEqual(_bower._read_api_key(), "ob_piped")

    def test_a_terminal_gets_a_hidden_prompt(self) -> None:
        stdin = mock.Mock()
        stdin.isatty.return_value = True
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch.object(secrets.sys, "stdin", stdin),
            mock.patch.object(secrets.getpass, "getpass", return_value="ob_typed") as prompt,
        ):
            self.assertEqual(_bower._read_api_key(), "ob_typed")
        prompt.assert_called_once()

    def test_ctrl_c_at_the_prompt_exits_130(self) -> None:
        stdin = mock.Mock()
        stdin.isatty.return_value = True
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch.object(secrets.sys, "stdin", stdin),
            mock.patch.object(secrets.getpass, "getpass", side_effect=KeyboardInterrupt),
            self.assertRaises(typer.Exit) as raised,
        ):
            _bower._read_api_key()
        self.assertEqual(raised.exception.exit_code, 130)


WEBHOOK = "https://api.example.io/v1/signals/01SIG/ingest/tok"


class PrintColumnsTests(unittest.TestCase):
    def test_a_column_with_a_key_shows_it_for_a_hand_written_map(self) -> None:
        response = BowerConnectResponse(
            dry_run=True,
            signal_label="",
            columns=[
                BowerConnectColumn(key="c_title", label="Title", type="text", field="title"),
                BowerConnectColumn(key="c_notes", label="Notes", type="text", field=""),
            ],
            unmatched_fields=["subreddit"],
        )
        with mock.patch.object(_bower, "console") as console:
            _bower._print_columns("Columns:", response)
        lines = [c.args[0] for c in console.log.call_args_list]
        # FAILS if the key is left out of the listing.
        self.assertIn("  Title [c_title] (text) <- title", lines)
        self.assertIn("Left blank (no item field matches): Notes [c_notes]", lines)
        self.assertIn("Not sent (no column takes them): subreddit", lines)

    def test_fields_left_out_are_listed(self) -> None:
        columns = [*COLUMNS, BowerConnectColumn(key="c_author", label="Author", type="text", field="")]
        response = BowerConnectResponse(
            dry_run=True, signal_label="Mentions", columns=columns, left_out_fields=["author", "handle"]
        )
        with mock.patch.object(_bower, "console") as console:
            _bower._print_columns("Columns:", response)
        lines = [c.args[0] for c in console.log.call_args_list]
        self.assertIn("Not sent (left out by --fields): author, handle", lines)
        # FAILS if a column left blank by --fields still says no field matches it.
        self.assertIn("Left blank (no chosen field matches): Author [c_author]", lines)

    def test_the_hint_names_fields_that_would_be_sent(self) -> None:
        columns = [
            BowerConnectColumn(label=name.title(), type="text", field=name) for name in ("title", "url", "a", "b")
        ]
        response = BowerConnectResponse(dry_run=True, signal_label="Mentions", columns=columns)
        with mock.patch.object(_bower, "console") as console:
            _bower._print_fields_hint(response)
        console.log.assert_called_once_with("Send fewer with --fields, e.g. --fields title,url,a")


class ConnectFlowTests(unittest.TestCase):
    def _run(self, **options: object) -> mock.Mock:
        api = self.api = mock.Mock()
        api.connect_bower.side_effect = lambda watch_id, body, dry_run=False: _preview() if dry_run else _created()
        args = {
            "webhook_url": False,
            "api_url": "https://api.example.io",
            "label": "",
            "fields": "",
            "digest": None,
            "rank": None,
            "dry_run": False,
            "yes": True,
        }
        args.update(options)
        with (
            mock.patch.object(_bower, "app_ctx") as ctx,
            mock.patch.object(_bower, "_read_api_key", return_value="ob_key") as read_key,
            mock.patch.object(_bower, "_read_webhook_url", return_value=WEBHOOK) as read_url,
            mock.patch.object(_bower, "console"),
            mock.patch.object(_bower, "_print_action_detail"),
            mock.patch.object(_bower, "_print_fields_hint") as hint,
        ):
            ctx.return_value.api.watch = api
            self.read_key, self.read_url, self.hint = read_key, read_url, hint
            _bower.connect_bower.__wrapped__("w1", **args)
        return api

    def test_it_previews_then_connects_with_the_key_in_the_body(self) -> None:
        api = self._run()
        self.assertEqual([c.kwargs.get("dry_run", False) for c in api.connect_bower.call_args_list], [True, False])
        body = api.connect_bower.call_args.args[1]
        self.assertIsInstance(body, BowerConnectNew)
        self.assertEqual(body.api_key, "ob_key")
        self.assertEqual(body.delivery, "instant")

    def test_each_route_shows_its_own_preview(self) -> None:
        for options, start in (({}, "Would create"), ({"webhook_url": True, "api_url": ""}, "Would send")):
            with self.subTest(start=start), mock.patch.object(_bower, "_print_columns") as shown:
                self._run(dry_run=True, **options)
            # FAILS if the copy no longer follows the request's route.
            self.assertTrue(shown.call_args.args[0].startswith(start))

    def test_a_dry_run_stops_after_the_preview(self) -> None:
        api = self._run(dry_run=True)
        self.assertEqual(api.connect_bower.call_count, 1)

    def test_fields_reach_the_request_as_a_list(self) -> None:
        body = self._run(fields=" title, url ,").connect_bower.call_args.args[1]
        self.assertEqual(body.fields, ["title", "url"])

    def test_a_dry_run_hints_at_fields_only_when_none_were_given(self) -> None:
        self._run(dry_run=True)
        self.hint.assert_called_once()
        self._run(dry_run=True, fields="title")
        self.hint.assert_not_called()

    def test_rank_reaches_the_request(self) -> None:
        body = self._run(rank=0).connect_bower.call_args.args[1]
        self.assertEqual(body.rank, 0)

    def test_digest_sets_the_cadence(self) -> None:
        body = self._run(digest=300).connect_bower.call_args.args[1]
        self.assertEqual((body.delivery, body.digest_interval_seconds), ("digest", 300))

    def test_a_webhook_url_connects_without_a_key(self) -> None:
        body = self._run(webhook_url=True, api_url="").connect_bower.call_args.args[1]
        # FAILS if the existing-signal route asks for a key or drops the URL.
        self.read_key.assert_not_called()
        self.assertEqual(body.webhook_url, WEBHOOK)
        self.assertIsInstance(body, BowerConnectExisting)

    def test_both_routes_or_neither_is_refused_before_reading(self) -> None:
        for options in ({"webhook_url": True}, {"api_url": ""}):
            with self.subTest(options=options), self.assertRaises(typer.Exit):
                self._run(**options)
            self.read_key.assert_not_called()
            self.read_url.assert_not_called()

    def test_a_label_with_a_webhook_url_is_refused(self) -> None:
        with self.assertRaises(typer.Exit):
            self._run(webhook_url=True, api_url="", label="Renamed")

    def _piped(self, **options: object) -> mock.Mock:
        stdin = mock.Mock()
        stdin.isatty.return_value = False
        # FAILS (rather than hanging) if a piped run reaches the confirm prompt.
        with (
            mock.patch.object(_bower.sys, "stdin", stdin),
            mock.patch.object(_bower.typer, "confirm", side_effect=AssertionError("prompted")),
        ):
            return self._run(**options)

    def test_piped_input_without_yes_refuses_before_reading_or_calling(self) -> None:
        with self.assertRaises(typer.Exit):
            self._piped(yes=False)
        # FAILS if the refusal comes only after the secret is read or the server previewed.
        self.read_key.assert_not_called()
        self.api.connect_bower.assert_not_called()

    def test_piped_input_may_dry_run_or_connect_with_yes(self) -> None:
        self.assertEqual(self._piped(yes=False, dry_run=True).connect_bower.call_count, 1)
        self.assertEqual(self._piped(yes=True).connect_bower.call_count, 2)


class ConnectErrorTests(unittest.TestCase):
    def test_a_bad_webhook_url_reads_as_its_own_message(self) -> None:
        with (
            mock.patch.object(_bower, "_read_webhook_url", return_value="not a url"),
            mock.patch("openmagpie.commands._shared.errors.console") as console,
            self.assertRaises(typer.Exit),
        ):
            _bower.connect_bower.__wrapped__(
                "w1", webhook_url=True, api_url="", label="", fields="", digest=None, rank=None, dry_run=True, yes=True
            )
        lines = [c.args[0] for c in console.error.call_args_list]
        self.assertEqual(lines[0], "Connect error:")
        # FAILS if pydantic's "Value error, " prefix reaches the user.
        self.assertTrue(lines[1].startswith("  webhook_url: url must be"), lines)


class OptionRangeTests(unittest.TestCase):
    def test_a_digest_or_rank_out_of_range_is_refused_before_reading(self) -> None:
        runner = CliRunner()
        for args in (["--digest", "0"], ["--digest", "-5"], ["--rank", "-1"]):
            with self.subTest(args=args), mock.patch.object(_bower, "_read_api_key") as read_key:
                result = runner.invoke(app, ["watch", "connect-bower", "w1", "--api-url", "https://h.example", *args])
                # FAILS if a bad number is caught only after the secret is read, or not at all.
                self.assertEqual(result.exit_code, 2, result.output)
                read_key.assert_not_called()


if __name__ == "__main__":
    unittest.main()
