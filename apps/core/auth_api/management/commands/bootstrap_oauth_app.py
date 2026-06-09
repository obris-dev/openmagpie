"""Create the singleton `Application` row used to mint tokens.

Idempotent, safe to run on every `local-migrate`. The Application's role
is storage only: our own /v1/auth/* views mint AccessToken / RefreshToken
rows against it directly, and we DO NOT expose Toolkit's `/oauth/*` HTTP
surface (see `core/conf/urls.py`). `authorization_grant_type` is set to
AUTHORIZATION_CODE rather than PASSWORD so a future accidental re-mount
of the URLs can't issue tokens from raw username+password.
"""

from typing import Any

from django.core.management.base import BaseCommand
from oauth2_provider.models import get_application_model

from auth_api.services.tokens import CLI_APPLICATION_NAME


class Command(BaseCommand):
    help = "Create the magpie-cli OAuth Application if missing (idempotent)."

    def handle(self, *args: Any, **options: Any) -> None:
        AppModel = get_application_model()
        app, created = AppModel.objects.get_or_create(
            name=CLI_APPLICATION_NAME,
            defaults={
                "client_type": AppModel.CLIENT_PUBLIC,
                "authorization_grant_type": AppModel.GRANT_AUTHORIZATION_CODE,
                "skip_authorization": True,
            },
        )
        verb = "Created" if created else "OAuth Application already exists:"
        self.stdout.write(f"{verb} {app.name} ({app.client_id})")
