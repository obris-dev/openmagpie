"""Transactional email: render via the email-render service, send via Django.

Templates are rendered out-of-process by the email-render service
(`EMAIL_RENDER_URL/render` -> `{html, plainText}`), then sent through Django's
configured `EMAIL_BACKEND`. In dev that backend is the console backend, so mail
prints to the runserver log and no SMTP creds are needed; prod points it at
Brevo SMTP via env. The render service owns the visual templates so they aren't
duplicated here.
"""

import logging

import httpx
from django.conf import settings
from django.core.mail import EmailMultiAlternatives

logger = logging.getLogger(__name__)


class EmailRenderError(Exception):
    """The email-render service failed to produce HTML for a template."""


class EmailService:
    """Render templates via the email-render service and send via the backend."""

    @staticmethod
    def render_template(*, template: str, props: dict) -> dict:
        """Render a template to `{"html", "plainText"}`. Raises EmailRenderError."""
        if not settings.EMAIL_RENDER_URL:
            raise EmailRenderError("EMAIL_RENDER_URL is not configured")
        url = f"{settings.EMAIL_RENDER_URL}/render"
        try:
            response = httpx.post(
                url,
                json={"template": template, "props": props},
                timeout=settings.EMAIL_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("success"):
                raise EmailRenderError(data.get("error", "Unknown render error"))
            return {"html": data["html"], "plainText": data["plainText"]}
        # JSONDecodeError (non-JSON 200) is a ValueError; missing html/plainText
        # is a KeyError. Convert all render failures to EmailRenderError so the
        # documented contract holds and callers can catch one type.
        except (httpx.HTTPError, ValueError, KeyError) as e:
            logger.error("Email render request failed: %s", e)
            raise EmailRenderError(f"Failed to render email template: {e}") from e

    @staticmethod
    def send(*, to_email: str, subject: str, html: str, plain_text: str) -> None:
        """Send a multipart (text + HTML) email via the configured backend."""
        msg = EmailMultiAlternatives(
            subject=subject,
            body=plain_text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to_email],
        )
        msg.attach_alternative(html, "text/html")
        msg.send()
        logger.info("Sent email to %s: %s", to_email, subject)

    @classmethod
    def send_template(cls, *, to_email: str, subject: str, template: str, props: dict) -> None:
        """Render a template and send it in one call."""
        rendered = cls.render_template(template=template, props=props)
        cls.send(
            to_email=to_email,
            subject=subject,
            html=rendered["html"],
            plain_text=rendered["plainText"],
        )
