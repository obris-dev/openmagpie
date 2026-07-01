"""RunFormatter.detail_fields per kind: the `magpie activity get` detail view.
The interactive detail must surface a kind's result blob, not a state-only row.
Stdlib unittest; run:
  uv run --package openmagpie python -m unittest discover -s apps/cli/tests
"""

from __future__ import annotations

import unittest

from openmagpie.commands.activity import ExtractRunFormatter
from openmagpie_schema.watch import WatchActionRunWire, build_watch_action_run_wire
from openmagpie_schema.watch_actions import ExternalContentStatus


def _run(result: dict) -> WatchActionRunWire:
    # `kind` selects the union member (ExtractRunWire), which validates the raw
    # result dict into a typed ExtractResult, mirroring the server builder.
    return build_watch_action_run_wire(
        kind="extract", id="01R", watch_id="01W", action_id="01A", feed_item_id="01F", state="succeeded", result=result
    )


class ExtractRunFormatterTests(unittest.TestCase):
    def test_surfaces_extracted_fields_status_and_enrichment(self) -> None:
        run = _run(
            {
                "extracted": {"company": "Acme", "event": "launch"},
                "status": "complete",
                "enrichment_status": ExternalContentStatus.INCLUDED.value,
            }
        )
        fields = dict(ExtractRunFormatter().detail_fields(run))
        self.assertEqual(fields["company"], "Acme")  # the hydrated fields, not just state
        self.assertEqual(fields["event"], "launch")
        self.assertEqual(fields["status"], "complete")  # completeness signal
        self.assertEqual(fields["enrichment"], ExternalContentStatus.INCLUDED.value)

    def test_not_applicable_enrichment_is_omitted(self) -> None:
        run = _run(
            {"extracted": {}, "status": "empty", "enrichment_status": ExternalContentStatus.NOT_APPLICABLE.value}
        )
        fields = dict(ExtractRunFormatter().detail_fields(run))
        self.assertNotIn("enrichment", fields)  # nothing to say -> omitted, like the filter formatter
