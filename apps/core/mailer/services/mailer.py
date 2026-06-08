"""Outbound-email queue: enqueue + the drain's claim/complete/reap primitives.

Not account-scoped (transactional mail isn't per-tenant) — static methods only.
Render + send happen in the DRAIN (see the send_outbound_emails command), never
here; `enqueue` is a pure DB insert so request handlers don't block on email.
The claim/complete/reap CAS mirrors `WatchActionRunService` (runs/_drain.py).
"""

from collections.abc import Iterator
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from ..constants import EmailState
from ..models import OutboundEmail


class MailerService:
    """Enqueue transactional email + the drain-side queue operations."""

    @staticmethod
    def enqueue(
        *,
        to_email: str,
        template: str,
        subject: str,
        props: dict | None = None,
        idempotency_key: str,
        scheduled_at: datetime | None = None,
    ) -> tuple[OutboundEmail, bool]:
        """Queue an email (PENDING). Idempotent on `idempotency_key`: a repeat
        enqueue returns the existing row (created=False), never a duplicate.
        Cheap insert only — no render/send here. The caller owns address
        validity: get_or_create doesn't run the EmailField's validators (today's
        only caller passes a serializer-validated address)."""
        defaults: dict = {
            "to_email": to_email.strip().lower(),
            "template": template,
            "subject": subject,
            "props": props or {},
        }
        if scheduled_at is not None:
            defaults["scheduled_at"] = scheduled_at
        return OutboundEmail.objects.get_or_create(idempotency_key=idempotency_key, defaults=defaults)

    @staticmethod
    def iter_due(*, now: datetime | None = None) -> Iterator[OutboundEmail]:
        """Emails eligible to send now, oldest first — READ-ONLY, no claim.

        "Due" = PENDING, under the attempts cap, with `scheduled_at` elapsed.
        The single source of the due-predicate: `claim_due` CASes each of these,
        the drain's dry-run previews them, so the two can't drift."""
        ts = now or timezone.now()
        return (
            OutboundEmail.objects.filter(
                state=EmailState.PENDING.value,
                scheduled_at__lte=ts,
                attempts__lt=settings.EMAIL_SEND_MAX_ATTEMPTS,
            )
            .order_by("scheduled_at")
            .iterator(chunk_size=100)
        )

    @classmethod
    def claim_due(cls, *, now: datetime | None = None, limit: int | None = None) -> Iterator[OutboundEmail]:
        """Yield due emails, each already CLAIMED (CAS to SENDING).

        Each `iter_due` candidate is claimed by a conditional UPDATE keyed on its
        still-PENDING state; only a row we actually flipped is yielded, so two
        concurrent drains never both send the same email. `limit` caps how many
        are CLAIMED — checked BEFORE the CAS, so a bounded pass never claims (and
        burns an attempt on) a row it won't go on to process."""
        ts = now or timezone.now()
        max_attempts = settings.EMAIL_SEND_MAX_ATTEMPTS
        claimed_count = 0
        for email in cls.iter_due(now=ts):
            if limit is not None and claimed_count >= limit:
                break
            claimed = OutboundEmail.objects.filter(
                id=email.id, state=EmailState.PENDING.value, attempts__lt=max_attempts
            ).update(state=EmailState.SENDING.value, started_at=ts, attempts=F("attempts") + 1)
            if claimed:
                claimed_count += 1
                email.refresh_from_db()
                yield email

    @staticmethod
    def reap_stale(*, now: datetime | None = None) -> int:
        """Recover SENDING rows orphaned by a crashed worker (claimed longer ago
        than the stale window). Rows still under the attempts cap go back to
        PENDING to retry; rows AT the cap (a crash on the final attempt) go to
        FAILED — otherwise they'd be stuck PENDING forever (claim_due needs
        attempts < cap) and never surface to a `state=FAILED` 'needs a human'
        query. Mirrors the watch-run reaper."""
        ts = now or timezone.now()
        max_attempts = settings.EMAIL_SEND_MAX_ATTEMPTS
        cutoff = ts - timedelta(seconds=settings.EMAIL_SEND_STALE_SECONDS)
        stale = OutboundEmail.objects.filter(state=EmailState.SENDING.value, started_at__lt=cutoff)
        # `.update()` bypasses auto_now, so set updated_at explicitly. The two
        # filters are disjoint on attempts, so order is irrelevant.
        exhausted = stale.filter(attempts__gte=max_attempts).update(
            state=EmailState.FAILED.value,
            error="reaped after stale timeout (attempts exhausted)",
            updated_at=ts,
        )
        retryable = stale.filter(attempts__lt=max_attempts).update(state=EmailState.PENDING.value, updated_at=ts)
        return exhausted + retryable

    @staticmethod
    def complete_sent(email: OutboundEmail, *, now: datetime | None = None) -> bool:
        """Mark a claimed (SENDING) row SENT. Guarded CAS on (state, attempts) of
        THIS claim: returns False if the claim was lost (reaped + re-claimed by
        another drain), so the stale worker doesn't clobber the fresh one."""
        ts = now or timezone.now()
        won = OutboundEmail.objects.filter(id=email.id, state=EmailState.SENDING.value, attempts=email.attempts).update(
            state=EmailState.SENT.value, sent_at=ts, error="", updated_at=ts
        )
        return bool(won)

    @staticmethod
    def fail(email: OutboundEmail, *, error: str, now: datetime | None = None) -> bool:
        """Record a send failure on a claimed (SENDING) row. Retries (back to
        PENDING with a backed-off `scheduled_at`) while under the attempts cap,
        else terminal FAILED. Guarded CAS on this claim's (state, attempts)."""
        ts = now or timezone.now()
        if email.attempts >= settings.EMAIL_SEND_MAX_ATTEMPTS:
            new_state = EmailState.FAILED.value
            scheduled_at = email.scheduled_at
        else:
            new_state = EmailState.PENDING.value
            scheduled_at = ts + timedelta(seconds=settings.EMAIL_SEND_RETRY_SECONDS)
        won = OutboundEmail.objects.filter(id=email.id, state=EmailState.SENDING.value, attempts=email.attempts).update(
            state=new_state, error=error, scheduled_at=scheduled_at, updated_at=ts
        )
        return bool(won)
