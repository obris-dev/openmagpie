"""`manage.py emit_quickstart_completed` -- fire the quickstart funnel's
completion event.

Run by scripts/quickstart/run.sh; telemetry is opt-out, so it sends on the default
and no-ops only if the operator turned it off (capture() gates on mode).
One-shot: emit + flush, because the process exits immediately and the SDK batches
captures on a background thread that would otherwise be torn down first.
"""

from django.core.management.base import BaseCommand

from ... import client, events
from ...constants import Surface


class Command(BaseCommand):
    help = "Emit the quickstart_completed telemetry event (no-op only when opted out)."

    def handle(self, *args, **options):
        # Server-internal emit (no request), so the surface is `system`, like the
        # scheduler's first_match -- not a bespoke out-of-allowlist value.
        events.quickstart_completed(surface=Surface.SYSTEM.value)
        client.flush()  # short-lived command: flush before the batch thread is torn down
