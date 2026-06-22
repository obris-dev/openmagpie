"""HTTP surface for the telemetry mode (under /v1/telemetry).

GET is auth-gated for any user, so a client (the CLI) can ask "has the operator
decided yet?" (mode == unset) and prompt. The setter is restricted to an account
OWNER: telemetry is an instance-wide operator decision, so a regular member must
not flip it. On self-hosted the signup/quickstart user owns their account, so they
qualify; a non-owner uses `manage.py telemetry enable/disable`. (On the future hosted product
the gate becomes OpenMagpie staff -- a customer-account owner must not flip the
SaaS-wide setting; that lands with the hosted build, alongside IDENTIFIED mode.)
"""

from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.services import AccountService, UserProfileService
from openmagpie_schema.telemetry import TelemetryState

from .models import TelemetrySettings
from .serializers import TelemetryConsentInput
from .service import TelemetryService


def _is_owner(user) -> bool:
    """Whether the user is the ACTIVE owner of their acting account -- the self-hosted
    operator who may set instance-wide telemetry. The acting account is resolved the
    standard way (the user's primary, as AccountScopedAPIView does); the ownership
    check is status + role on THAT account (a revoked/pending owner doesn't qualify).
    is_staff is deliberately NOT used: it's reserved for OpenMagpie's own staff on the
    hosted product."""
    account_id = AccountService.Global.primary_account_id_for(user_id=str(user.id))
    return account_id is not None and UserProfileService.Global.is_active_owner(
        user_id=str(user.id), account_id=account_id
    )


class TelemetryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        # can_set lets a client (the CLI) prompt only the owner who can act on it.
        state = TelemetryState(mode=TelemetrySettings.current().mode, can_set=_is_owner(request.user))
        return Response(state.model_dump())

    def post(self, request):
        if not _is_owner(request.user):
            return Response(
                {"detail": "Telemetry is an instance-wide setting; only an account owner can change it."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = TelemetryConsentInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = TelemetryService.Global.set_enabled(enabled=serializer.validated_data["enabled"])
        # can_set is True: we just passed the owner gate above.
        return Response(TelemetryState(mode=row.mode, can_set=True).model_dump())
