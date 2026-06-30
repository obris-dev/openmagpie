"""`manage.py telemetry enable | disable | status`.

The operator-facing switch for anonymous, opt-out product telemetry. enable/disable
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

# The subcommand verbs, defined once and reused by add_arguments + handle so the
# dispatch can't drift from the parser (no bare verb literal in the comparison).
_ENABLE, _DISABLE, _STATUS = "enable", "disable", "status"


class Command(BaseCommand):
    help = f"Manage anonymous, opt-out product telemetry (see {_DOC})."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        sub.add_parser(_ENABLE, help="turn on anonymous telemetry")
        sub.add_parser(_DISABLE, help="turn off telemetry")
        sub.add_parser(_STATUS, help="show the current telemetry mode and what is collected")

    def handle(self, *args, **options):
        action = options["action"]
        if action in (_ENABLE, _DISABLE):
            row = TelemetryService.Global.set_enabled(enabled=action == _ENABLE)
            self.stdout.write(self.style.SUCCESS(f"telemetry {action}d (mode: {row.mode})"))
            if row.is_anonymous:
                self.stdout.write(
                    f"Thanks for sharing anonymous usage. Turn it off any time: telemetry disable (see {_DOC})."
                )
            return
        self._status()

    def _status(self) -> None:
        row = TelemetrySettings.current()
        do_not_track = client.do_not_track()
        has_key = bool(settings.POSTHOG_API_KEY)
        # The ONE source of truth for "is it emitting?" -- opt-out: emits unless OFF,
        # DO_NOT_TRACK, or no key. Do NOT re-derive it here (a private re-spelling is
        # exactly what drifted from `is_anonymous` to the opt-out gate). The locals
        # above stay only to print WHY it's off (the DO_NOT_TRACK / no-key notes).
        emitting = client.enabled()
        self.stdout.write(f"mode:          {row.mode}")
        self.stdout.write(f"emitting:      {'yes' if emitting else 'no'}")
        if do_not_track:
            self.stdout.write("               (DO_NOT_TRACK is set, so nothing is sent regardless of mode)")
        if not has_key:
            self.stdout.write("               (no POSTHOG_API_KEY configured, so nothing is sent)")
        if row.instance_id:
            self.stdout.write(f"instance_id:   {row.instance_id}  (random, not linked to any account)")
        self.stdout.write(f"what is sent:  anonymous usage events only, never your content. See {_DOC}.")
