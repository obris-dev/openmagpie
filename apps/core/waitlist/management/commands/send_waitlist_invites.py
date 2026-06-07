"""Send early-access invites to PENDING waitlist signups (oldest first).

Manual / operator-driven: there's no schedule. Each invited row flips to
INVITED only after its email sends, so a mid-batch failure is safe to re-run.
Use `--limit` to roll out in waves and `--dry-run` to preview the batch.
"""

import logging
from typing import Any

from django.core.management.base import CommandParser

from common.commands import SingleFlightCommand
from waitlist.services import WaitlistService

logger = logging.getLogger("waitlist")


# Sends real email + flips state, so two concurrent runs would double-send.
# SingleFlightCommand holds job_lock("waitlist.send_waitlist_invites") for the
# run; a second launch logs + skips instead of piling on.
class Command(SingleFlightCommand):
    help = "Send early-access invites to pending waitlist signups (oldest first)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Max invites to send this run (default: all pending)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List who would be invited without sending or changing state",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        limit = options["limit"]
        dry_run = options["dry_run"]

        sent = 0
        failed = 0
        for signup in WaitlistService.iter_pending():
            if limit is not None and sent >= limit:
                break

            if dry_run:
                self.stdout.write(f"  would invite {signup.email}")
                sent += 1
                continue

            # Per-signup try/except (matches the other SingleFlightCommands): a
            # single failed invite (render/SMTP error, etc.) must NOT abort the
            # batch — it stays PENDING for the next run. Broad on purpose; the
            # traceback is captured via logger.exception.
            try:
                WaitlistService.mark_invited(signup)
                self.stdout.write(f"  invited {signup.email}")
                sent += 1
            except Exception as e:
                failed += 1
                logger.exception("invite failed for %s: %s", signup.email, e)
                self.stderr.write(f"  failed {signup.email}: {e}")

        verb = "would invite" if dry_run else "invited"
        summary = f"\n{verb} {sent} signup(s)"
        if failed:
            summary += f", {failed} failed"
        self.stdout.write(summary)
