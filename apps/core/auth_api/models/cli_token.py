"""Personal access tokens for the `magpie` CLI (`CliToken`).

A long-lived, named, revocable credential an operator mints server-side
(the `issue_cli_token` management command, or the `/v1/auth/cli-tokens`
endpoints) and feeds to the CLI on a headless box, no browser device-flow
required.

Why a dedicated model rather than a long-lived OAuth `AccessToken`:
  - Hashed at rest. The raw token is shown once at creation and only its
    SHA-256 is stored, so a DB leak doesn't hand over standing access.
    (Session `AccessToken`s are short-lived and stored plaintext; a PAT
    lives for months, so the exposure math is different.)
  - Named + individually revocable, so an operator running several boxes
    can tell tokens apart and revoke one without nuking the rest.

Scoping: a CliToken is a USER-GLOBAL auth credential, not account-scoped.
It carries `user_id` but deliberately NO `account_id` (the one documented
exception to the "every domain model carries account_id + user_id" rule
in AGENTS.md). This mirrors the OAuth session token, which is bound to a
user, not an account: the active account is resolved per request from the
user's primary account (see `UserSerializer.get_account_id`), so pinning
an account into the credential would diverge from how sessions behave.

`user_id` is a char pointer, not a `ForeignKey` (per AGENTS.md): no
cascade, so a future user-deletion service would own cleanup of its
tokens (none deletes users today).

Resolution lives in `services/cli_tokens.py`; the DRF auth class
`PersonalAccessTokenAuthentication` routes `Bearer mgp_...` headers there.
A PAT can NOT mint or otherwise manage other tokens, `POST /v1/auth/
cli-tokens` requires a session login, so a leaked token can't bootstrap
fresh credentials that survive its own revocation.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel


class CliToken(BaseModel):
    """A hashed personal access token bound to one user."""

    user_id = models.CharField(
        _("user id"),
        max_length=26,
        db_index=True,
        help_text=_("The id of the user this token authenticates as."),
    )
    name = models.CharField(
        _("name"),
        max_length=255,
        help_text=_("Human label so the owner can tell tokens apart (e.g. 'home-office box')."),
    )
    # SHA-256 hex digest of the raw token. 64 chars, unique + indexed so
    # resolution is a single point lookup. The raw token is never stored.
    token_hash = models.CharField(_("token hash"), max_length=64, unique=True)
    # Last 4 chars of the raw token, for display only (`mgp_...a1b2`). Not
    # secret on its own and far too short to brute-force the rest from.
    last_four = models.CharField(_("last four"), max_length=4)
    last_used_at = models.DateTimeField(_("last used at"), null=True, blank=True)
    # Null = never expires (the default). Operators opt into a bound via
    # `--expires-in-days`.
    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True)
    revoked_at = models.DateTimeField(_("revoked at"), null=True, blank=True)

    class Meta:
        verbose_name = _("CLI token")
        verbose_name_plural = _("CLI tokens")

    def __str__(self) -> str:
        return f"{self.name} (mgp_...{self.last_four})"

    def is_valid(self) -> bool:
        """True iff the token is neither revoked nor past its expiry.

        Mirrors OAuth Toolkit's `AccessToken.is_valid()` semantics so the
        two credential types behave the same to a caller.
        """
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()
