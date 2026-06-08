"""Drain the outbound-email queue: render + send PENDING rows, with retries.

Scheduled (cron / up-jobs ticker). SingleFlightCommand so overlapping passes
skip rather than double-send. Each pass first reaps SENDING rows orphaned by a
crash, then claims due rows (CAS) and sends them. Per-row try/except so one bad
address never aborts the batch (matches process_due_runs / poll_due_feeds).

At-least-once: SENT is marked AFTER the send returns, so a crash between
SMTP-accept and the mark re-sends on the next pass. Acceptable for transactional
mail.
"""

import logging
from typing import Any

from django.core.management.base import CommandParser
from django.utils import timezone

from common.commands import SingleFlightCommand
from common.email import EmailService
from mailer.services import MailerService

logger = logging.getLogger("mailer")


class Command(SingleFlightCommand):
    help = "Render + send queued outbound emails (with retries)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Max emails to send this pass (default: all due)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List which emails would be sent without claiming or sending",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        limit = options["limit"]
        dry_run = options["dry_run"]
        now = timezone.now()

        # Dry-run previews due rows WITHOUT claiming (claim_due mutates as it
        # streams), so it can't reuse the drain path.
        if dry_run:
            self._dry_run(limit)
            return

        reaped = MailerService.reap_stale(now=now)
        if reaped:
            logger.info("reaped %d stale sending email(s)", reaped)

        sent = 0
        failed = 0
        # `limit` is passed INTO claim_due so it stops claiming at the boundary;
        # a caller-side break would claim (and burn an attempt on) one extra row
        # before stopping, leaving it SENDING until the reaper recovers it.
        for email in MailerService.claim_due(now=now, limit=limit):
            # ONLY the send is in the fail()-on-error try: a render/SMTP error
            # requeues (or caps) the row. A single failure must not abort the
            # batch.
            try:
                EmailService.send_template(
                    to_email=email.to_email,
                    subject=email.subject,
                    template=email.template,
                    props=email.props,
                )
            except Exception as e:
                failed += 1
                MailerService.fail(email, error=f"{type(e).__name__}: {e}")
                logger.exception("send failed for %s (email=%s): %s", email.to_email, email.id, e)
                self.stderr.write(f"  failed {email.to_email}: {e}")
                continue

            # Post-send bookkeeping, OUTSIDE the send try on purpose: the email
            # already went out, so a complete_sent failure must NOT call fail()
            # (that would re-send a delivered email). Leave the row SENDING and
            # let the reaper recover it — at-least-once, never an active requeue.
            try:
                MailerService.complete_sent(email)
            except Exception as e:
                logger.exception("complete_sent failed after send for %s (email=%s): %s", email.to_email, email.id, e)
            sent += 1
            self.stdout.write(f"  sent {email.template} -> {email.to_email}")

        summary = f"\nsent {sent} email(s)"
        if failed:
            summary += f", {failed} failed (retrying / capped)"
        self.stdout.write(summary)

    def _dry_run(self, limit: int | None) -> None:
        """Preview due rows via the same predicate claim_due uses, WITHOUT
        claiming (no state change, no send)."""
        n = 0
        for email in MailerService.iter_due():
            if limit is not None and n >= limit:
                break
            self.stdout.write(f"  would send {email.template} -> {email.to_email}")
            n += 1
        self.stdout.write(f"\nwould send {n} email(s)")
