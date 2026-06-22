"""`manage.py emit_quickstart_completed` -- fire the quickstart funnel's
completion event.

Run by scripts/quickstart/run.sh AFTER the telemetry consent prompt, so it only
sends if the operator opted in (capture() gates on mode; otherwise it's a no-op).
One-shot: emit + flush, because the process exits immediately and the SDK batches
captures on a background thread that would otherwise be torn down first.
"""

from django.core.management.base import BaseCommand

from ... import client, events
from ...constants import Surface


class Command(BaseCommand):
    help = "Emit the quickstart_completed telemetry event (no-op unless opted in)."

    def handle(self, *args, **options):
        # Server-internal emit (no request), so the surface is `system`, like the
        # scheduler's first_match -- not a bespoke out-of-allowlist value.
        events.quickstart_completed(surface=Surface.SYSTEM.value)
        client.flush()  # short-lived command: flush before the batch thread is torn down
