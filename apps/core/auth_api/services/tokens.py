"""Mint OAuth Toolkit token pairs for our own auth endpoints.

We don't use OAuth Toolkit's grant flows directly, signup/login mint tokens
inline against a single bootstrap `Application` ("magpie-cli"). Toolkit's
HTTP surface (`/oauth/*`) is intentionally NOT mounted in `conf/urls.py`
because exposing it would let anyone POST a password / client_credentials
grant and bypass our login/audit pipeline. Only the Toolkit models are in
play here, as a typed storage layer for the tokens.

`mint_token_pair_for_user(user)` is the single seam: signup, login, refresh
and device-flow completion all funnel through it so the token shape stays
identical across surfaces.
"""

import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from oauth2_provider.models import (
    AccessToken,
    Application,
    RefreshToken,
    get_application_model,
)

CLI_APPLICATION_NAME = "magpie-cli"

DEFAULT_ACCESS_TTL_SECONDS = 3600


def _access_ttl_seconds() -> int:
    return int(settings.OAUTH2_PROVIDER.get("ACCESS_TOKEN_EXPIRES_SECONDS", DEFAULT_ACCESS_TTL_SECONDS))


def get_cli_application() -> Application:
    """Return the singleton CLI `Application` row.

    Raises `Application.DoesNotExist` if not bootstrapped, callers should
    have run `manage.py bootstrap_oauth_app` (or `make local-migrate`) first.
    """
    AppModel = get_application_model()
    return AppModel.objects.get(name=CLI_APPLICATION_NAME)


@transaction.atomic
def revoke_access_token(token_value: str) -> None:
    """Revoke the named access token + its refresh-token pair.

    No-op if the token doesn't exist or is already revoked. Called from
    /v1/auth/logout so a CLI logout (or a logged-out browser) actually
    invalidates the credentials server-side, not just locally.

    Atomic + row-locked: two concurrent logouts for the same token (a
    user clicking logout twice, or a parallel CLI revoke + browser
    logout) would otherwise race on "is the refresh already revoked"
    and one could try to revoke after the other had deleted the access
    row, producing an inconsistent half-state. The `select_for_update`
    serializes them; the second caller sees the access row gone and
    no-ops cleanly.
    """
    try:
        access = AccessToken.objects.select_for_update().get(token=token_value)
    except AccessToken.DoesNotExist:
        return
    try:
        refresh = RefreshToken.objects.select_for_update().get(access_token=access)
    except RefreshToken.DoesNotExist:
        refresh = None
    if refresh is not None and refresh.revoked is None:
        refresh.revoke()
    access.delete()


def mint_token_pair_for_user(user) -> tuple[AccessToken, RefreshToken, int]:
    """Create a fresh (access, refresh) pair against the CLI application.

    Returns the two Toolkit rows plus `expires_in` seconds, sufficient to
    build the response body the web + CLI clients expect.
    """
    application = get_cli_application()
    ttl = _access_ttl_seconds()
    now = timezone.now()

    access = AccessToken.objects.create(
        user=user,
        application=application,
        token=secrets.token_urlsafe(48),
        expires=now + timedelta(seconds=ttl),
        scope=" ".join(settings.OAUTH2_PROVIDER.get("DEFAULT_SCOPES", ["read", "write"])),
    )
    refresh = RefreshToken.objects.create(
        user=user,
        application=application,
        token=secrets.token_urlsafe(48),
        access_token=access,
    )
    return access, refresh, ttl
