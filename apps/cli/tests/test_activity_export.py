"""Unit tests for `magpie activity export`: the default-column resolution per kind,
the `result.*` column expansion, the page-by-page CSV/NDJSON stream, and the paged
record drain. The run-window flags live in test_window_flags.py. No live server; the
activity API is mocked. Stdlib `unittest`; run:
  uv run --package openmagpie python -m unittest discover -s apps/cli/tests
"""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import typer

from openmagpie.commands._shared import _emit_columns_stream, col
from openmagpie.commands.activity_export import (
    _RESULT_MODELS,
    _USER_DECLARED_COLUMN_KINDS,
    _default_columns_for_action,
    _iter_records,
    _result_columns,
)
from openmagpie_schema.watch import RunFeedItem, build_watch_action_run_wire, build_watch_action_wire
from openmagpie_schema.watch_actions import LogResult, SemanticFilterResult, WebhookResult
from openmagpie_schema.watch_enums import WatchActionKind


def _record(extracted: dict[str, str], *, title: str = "T") -> dict:
    """A run record shaped like `_run_record` returns ({run, feed_item, ...})."""
    return {
        "run": {"id": "01R", "state": "succeeded", "result": {"extracted": extracted, "status": "complete"}},
        "feed_item": {"title": title, "url": "http://u", "external_url": "http://e"},
        "feed": None,
        "action": None,
    }


class ColumnsForActionTests(unittest.TestCase):
    def test_extract_action_columns_from_declared_fields(self) -> None:
        # Deterministic from config.fields - NOT dependent on page-1 rows. A real
        # extract wire (config is the typed ExtractConfig union member, not a dict).
        action = build_watch_action_wire(
            kind="extract",
            rank=0,
            config={"fields": [{"name": "company", "description": "x"}, {"name": "event", "description": "y"}]},
        )
        cols = _default_columns_for_action(action)([])  # deterministic resolver ignores the page
        paths = [c.path for c in cols]
        headers = [c.header for c in cols]
        self.assertIn("run.result.extracted.company", paths)
        self.assertIn("run.result.extracted.event", paths)
        self.assertIn("COMPANY", headers)  # header uppercased, matching the fixed columns
        # extract's fixed completeness/provenance columns are present (not just the
        # declared fields), so an empty extraction is distinguishable from a full one.
        self.assertIn("run.result.status", paths)
        self.assertIn("run.result.enrichment_status", paths)

    def test_known_non_extract_kind_columns_from_result_model(self) -> None:
        # Deterministic from each kind's Result model -- every field projects, never
        # dependent on page-1 rows (the protection extract already had).
        for kind, model in {
            "semantic_filter": SemanticFilterResult,
            "log": LogResult,
            "webhook": WebhookResult,
        }.items():
            cols = _default_columns_for_action(SimpleNamespace(kind=kind, config={}))([])
            paths = {c.path for c in cols}
            for field in model.model_fields:
                self.assertIn(f"run.result.{field}", paths, f"{kind}.{field}")

    def test_deterministic_kind_ignores_the_page_unknown_reflects_it(self) -> None:
        # The resolver is uniform now: a known kind ignores the page (deterministic
        # header), an unknown kind reflects whatever result keys the page carries.
        page = [{"run": {"result": {"widget": 1}}, "feed_item": None, "feed": None, "action": None}]
        known = _default_columns_for_action(SimpleNamespace(kind="semantic_filter", config={}))
        self.assertEqual([c.path for c in known(page)], [c.path for c in known([])])  # ignores the page
        unknown = _default_columns_for_action(SimpleNamespace(kind="future_kind", config={}))
        self.assertIn("run.result.widget", {c.path for c in unknown(page)})  # reflects the page

    def test_extract_is_the_only_user_declared_columns_kind(self) -> None:
        # Locks the "only extract declares its own output columns" assumption; adding
        # another must consciously update this.
        self.assertEqual(_USER_DECLARED_COLUMN_KINDS, {WatchActionKind.EXTRACT})

    def test_every_known_kind_is_categorized_exactly_once(self) -> None:
        # Completeness tripwire (here, not a -O-strippable import-time assert): every
        # kind is user-declared OR fixed-shape, with no overlap. A new kind that isn't
        # slotted into one of the two maps (both enum-keyed) fails here.
        self.assertEqual(_USER_DECLARED_COLUMN_KINDS | set(_RESULT_MODELS), set(WatchActionKind))
        self.assertFalse(_USER_DECLARED_COLUMN_KINDS & set(_RESULT_MODELS))  # no kind in both


class ResultColumnsTests(unittest.TestCase):
    def test_extracted_fields_become_columns_plain_keys_first(self) -> None:
        cols = _result_columns([_record({"person": "Pat", "org": "Acme"})])
        headers = [c.header for c in cols]
        paths = [c.path for c in cols]
        # plain result key (status) before the extracted fields
        self.assertEqual(headers[0], "STATUS")
        self.assertIn("PERSON", headers)
        self.assertIn("run.result.extracted.person", paths)
        self.assertIn("run.result.extracted.org", paths)

    def test_union_across_rows_is_stable(self) -> None:
        cols = _result_columns([_record({"person": "Pat"}), _record({"person": "X", "org": "Y"})])
        paths = [c.path for c in cols]
        self.assertIn("run.result.extracted.person", paths)
        self.assertIn("run.result.extracted.org", paths)  # picked up from the 2nd row


class StreamRenderTests(unittest.TestCase):
    """`_emit_columns_stream`: CSV/NDJSON page by page, header from the first page."""

    def _stream(self, pages, default_columns, *, jsonl: bool = False, columns: str | None = None) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            _emit_columns_stream(
                pages=pages,
                record_of=lambda r: r,
                default_columns=default_columns,
                columns=columns,
                jsonl=jsonl,
                output=None,
            )
        return buf.getvalue()

    def test_csv_header_from_first_page_then_rows(self) -> None:
        pages = [[_record({"person": "Pat", "org": "Acme"})]]
        out = self._stream(pages, lambda recs: [col("TITLE:feed_item.title"), *_result_columns(recs)])
        rows = list(csv.reader(io.StringIO(out)))
        self.assertEqual(rows[0], ["TITLE", "STATUS", "PERSON", "ORG"])  # header off page 1
        self.assertEqual(rows[1], ["T", "complete", "Pat", "Acme"])

    def test_csv_streams_multiple_pages_under_one_header(self) -> None:
        pages = [[_record({"person": "A"}, title="t1")], [_record({"person": "B"}, title="t2")]]
        out = self._stream(
            pages, lambda recs: [col("TITLE:feed_item.title"), col("PERSON:run.result.extracted.person")]
        )
        rows = list(csv.reader(io.StringIO(out)))
        self.assertEqual(rows[0], ["TITLE", "PERSON"])  # one header for both pages
        self.assertEqual([r[0] for r in rows[1:]], ["t1", "t2"])  # both pages' rows present

    def test_returns_rows_written_excluding_header(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            written = _emit_columns_stream(
                pages=[[_record({"person": "A"})], [_record({"person": "B"})]],
                record_of=lambda r: r,
                default_columns=lambda recs: [col("TITLE:feed_item.title")],
                columns=None,
                jsonl=False,
                output=None,
            )
        self.assertEqual(written, 2)  # two data rows, header not counted

    def test_empty_writes_header_only(self) -> None:
        rows = list(csv.reader(io.StringIO(self._stream([], lambda recs: [col("TITLE:feed_item.title")]))))
        self.assertEqual(rows, [["TITLE"]])

    def test_csv_dedupes_colliding_headers(self) -> None:
        # Two columns whose headers collide (e.g. an extracted field named like a
        # fixed column) must not silently collapse on read: the dup gets a suffix.
        out = self._stream([[_record({"x": "v"})]], [col("DUP:feed_item.title"), col("DUP:run.state")])
        self.assertEqual(next(iter(csv.reader(io.StringIO(out)))), ["DUP", "DUP_2"])

    def test_csv_dedupes_literal_vs_generated_suffix(self) -> None:
        # A literal "X_2" must not collide with the "X_2" generated from a second
        # "X"; the generated names are registered too, so all three stay distinct.
        out = self._stream(
            [[_record({"x": "v"})]],
            [col("X:feed_item.title"), col("X:run.state"), col("X_2:feed_item.url")],
        )
        self.assertEqual(next(iter(csv.reader(io.StringIO(out)))), ["X", "X_2", "X_2_2"])

    def test_csv_dedupes_headers_case_insensitively(self) -> None:
        # Collision detection folds case (per the contract), so a user's "state"
        # can't silently collapse against the fixed STATE on read.
        out = self._stream([[_record({"x": "v"})]], [col("STATE:run.state"), col("state:feed_item.title")])
        self.assertEqual(next(iter(csv.reader(io.StringIO(out)))), ["STATE", "state_2"])

    def test_csv_neutralizes_formula_injection(self) -> None:
        # An item title that starts with `=` (or + - @) must not export as a live
        # spreadsheet formula: it's prefixed with a quote. NDJSON stays exact.
        pages = [[_record({"person": "ok"}, title="=HYPERLINK(evil)")]]
        out = self._stream(pages, lambda recs: [col("TITLE:feed_item.title")])
        rows = list(csv.reader(io.StringIO(out)))
        self.assertEqual(rows[1], ["'=HYPERLINK(evil)"])  # leading quote neutralizes the formula

    def test_csv_empty_cell_stays_sentinel_not_quoted(self) -> None:
        # The empty-cell sentinel "-" must NOT be neutralized to "'-" (it starts
        # with `-` but it's our own marker, not a formula). Empty cells are the norm.
        out = self._stream([[_record({"person": "Pat"})]], [col("MISSING:run.result.nope")])
        rows = list(csv.reader(io.StringIO(out)))
        self.assertEqual(rows[1], ["-"])  # the sentinel, NOT "'-"

    def test_csv_negative_number_stays_exact(self) -> None:
        # A leading `-` on a real number is data, not a formula -> not quoted.
        out = self._stream([[_record({"val": "-12.5"})]], [col("VAL:run.result.extracted.val")])
        self.assertEqual(list(csv.reader(io.StringIO(out)))[1], ["-12.5"])  # exact, NOT "'-12.5"

    def test_csv_minus_led_non_number_is_still_neutralized(self) -> None:
        # A `-`-led cell that isn't numeric (a real formula) is still quoted.
        out = self._stream([[_record({"val": "-1+cmd()"})]], [col("VAL:run.result.extracted.val")])
        self.assertEqual(list(csv.reader(io.StringIO(out)))[1], ["'-1+cmd()"])

    def test_jsonl_does_not_neutralize(self) -> None:
        out = self._stream([[_record({}, title="=evil")]], lambda recs: [], jsonl=True)
        self.assertEqual(json.loads(out.splitlines()[0])["feed_item"]["title"], "=evil")  # byte-exact

    def test_csv_to_file_roundtrips_embedded_newline_and_unicode(self) -> None:
        # The real-file path: a quoted field with an embedded newline round-trips
        # (csv owns line endings, CR not doubled), AND non-ASCII survives -- the file
        # is opened utf-8, not the locale default (extract values carry unicode).
        rec = _record({"note": "café\nrésumé"})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.csv")
            _emit_columns_stream(
                pages=[[rec]],
                record_of=lambda r: r,
                default_columns=lambda recs: [col("NOTE:run.result.extracted.note")],
                columns=None,
                jsonl=False,
                output=path,
            )
            with open(path, encoding="utf-8", newline="") as fh:
                rows = list(csv.reader(fh))
            raw = Path(path).read_bytes()
        self.assertEqual(rows[1], ["café\nrésumé"])  # embedded newline + unicode preserved
        self.assertNotIn(b"\r\r\n", raw)  # csv owns the line ending; CR not doubled
        self.assertIn("é".encode(), raw)  # written as UTF-8 bytes, not the locale default

    def test_jsonl_streams_records(self) -> None:
        out = self._stream([[_record({"person": "Pat"})]], lambda recs: [], jsonl=True)
        lines = [json.loads(line) for line in out.splitlines() if line.strip()]
        self.assertEqual(lines[0]["run"]["result"]["extracted"], {"person": "Pat"})

    def test_jsonl_with_columns_is_rejected(self) -> None:
        with self.assertRaises(typer.BadParameter):
            self._stream([], lambda recs: [], jsonl=True, columns="feed_item.title")


class IterRecordsTests(unittest.TestCase):
    def _resp(self, ids: list[str], next_cursor: str | None) -> SimpleNamespace:
        items = [
            build_watch_action_run_wire(
                kind="log", id=i, watch_id="w", action_id="a", feed_item_id=f"itm-{i}", state="succeeded"
            )
            for i in ids
        ]
        feed_items = {f"itm-{i}": RunFeedItem(feed_id="f", title=f"t{i}") for i in ids}
        return SimpleNamespace(items=items, next_cursor=next_cursor, feed_items=feed_items, feeds={}, action=None)

    def _ctx(self, api) -> SimpleNamespace:
        return SimpleNamespace(api=SimpleNamespace(activity=api))

    def test_yields_pages_and_paginates(self) -> None:
        api = mock.Mock()
        api.list.side_effect = [self._resp(["01A", "01B"], "01B"), self._resp(["01C"], None)]
        with mock.patch("openmagpie.commands.activity_export.app_ctx", return_value=self._ctx(api)):
            pages = list(_iter_records("a", state=None, windows={}))
        self.assertEqual(
            [[r["run"]["feed_item_id"] for r in page] for page in pages],
            [["itm-01A", "itm-01B"], ["itm-01C"]],
        )
        self.assertEqual(api.list.call_args_list[1].kwargs["after"], "01B")  # paged after page 1's cursor

    def test_passes_window_kwargs_through(self) -> None:
        api = mock.Mock()
        api.list.return_value = self._resp([], None)
        with mock.patch("openmagpie.commands.activity_export.app_ctx", return_value=self._ctx(api)):
            list(_iter_records("a", state="succeeded", windows={"occurred_since": "2026-06-01T00:00:00+00:00"}))
        kwargs = api.list.call_args.kwargs
        self.assertEqual(kwargs["state"], "succeeded")
        self.assertEqual(kwargs["occurred_since"], "2026-06-01T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
