"""`manage.py telemetry enable | disable | status`.

The operator-facing switch for anonymous, opt-in product telemetry. enable/disable
share `service.set_enabled` with the HTTP endpoint (the server resolves the intent
to a mode; self-hosted: enable -> anonymous). `identified` is never settable here
(hosted-only). See apps/core/TELEMETRY.md for exactly what is collected.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from ... import client
from ...models import TelemetrySettings
from ...service import TelemetryService

_DOC = "apps/core/TELEMETRY.md"


class Command(BaseCommand):
    help = "Manage anonymous, opt-in product telemetry (see apps/core/TELEMETRY.md)."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        sub.add_parser("enable", help="turn on anonymous telemetry")
        sub.add_parser("disable", help="turn off telemetry")
        sub.add_parser("status", help="show the current telemetry mode and what is collected")

    def handle(self, *args, **options):
        action = options["action"]
        if action in ("enable", "disable"):
            row = TelemetryService.Global.set_enabled(enabled=action == "enable")
            self.stdout.write(self.style.SUCCESS(f"telemetry {action}d (mode: {row.mode})"))
            if row.is_anonymous:
                self.stdout.write(
                    "Thanks for sharing anonymous usage. Turn it off any time: telemetry disable (see apps/core/TELEMETRY.md)."
                )
            return
        self._status()

    def _status(self) -> None:
        row = TelemetrySettings.current()
        do_not_track = client.do_not_track()
        has_key = bool(settings.POSTHOG_API_KEY)
        emitting = row.is_anonymous and not do_not_track and has_key
        self.stdout.write(f"mode:          {row.mode}")
        self.stdout.write(f"emitting:      {'yes' if emitting else 'no'}")
        if do_not_track:
            self.stdout.write("               (DO_NOT_TRACK is set, so nothing is sent regardless of mode)")
        if not has_key:
            self.stdout.write("               (no POSTHOG_API_KEY configured, so nothing is sent)")
        if row.instance_id:
            self.stdout.write(f"instance_id:   {row.instance_id}  (random, not linked to any account)")
        self.stdout.write(f"what is sent:  anonymous usage events only, never your content. See {_DOC}.")
