from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel

from ..constants import EmailState


class OutboundEmail(BaseModel):
    """A queued transactional email: rendered + sent out-of-request by the
    `send_outbound_emails` drain, never inline in a web request.

    Callers `MailerService.enqueue(...)` a PENDING row (a cheap insert) and
    return; the drain claims it (CAS to SENDING, attempts++), renders via the
    email-render service, sends via Django's backend, and marks SENT — or, on a
    transient failure under the attempts cap, schedules a retry (back to PENDING
    with a future `scheduled_at`), else FAILED. `idempotency_key` is unique so
    enqueue is idempotent (e.g. one welcome per signup). `state` is a bare
    CharField (see `mailer.constants`). No FKs; the key encodes any origin.
    """

    to_email = models.EmailField(_("to email"))
    template = models.CharField(_("template"), max_length=64, help_text=_("Render-service template name"))
    subject = models.CharField(_("subject"), max_length=255)
    props = models.JSONField(_("props"), default=dict, help_text=_("Template props passed to the renderer"))

    idempotency_key = models.CharField(_("idempotency key"), max_length=255, unique=True)
    state = models.CharField(_("state"), max_length=32, default=EmailState.PENDING.value)
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=0)
    error = models.TextField(_("error"), blank=True, default="")

    # When the row becomes eligible to send (now by default; pushed forward on a
    # retry). `started_at` stamps the claim, so the reaper can spot a SENDING row
    # orphaned by a crashed worker. `sent_at` is set on terminal success.
    scheduled_at = models.DateTimeField(_("scheduled at"), default=timezone.now)
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)

    class Meta:
        verbose_name = _("outbound email")
        verbose_name_plural = _("outbound emails")
        indexes = [
            # The drain's due query: state + scheduled_at (oldest-first).
            models.Index(fields=["state", "scheduled_at"], name="outboundemail_state_sched_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.template} -> {self.to_email} [{self.state}] ({self.id})"
