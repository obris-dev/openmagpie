"""HTTP entry point for the public waitlist.

One public, unauthenticated endpoint (`permission_classes = []`). It's
IP-throttled via a scoped throttle (rate set under `waitlist` in
REST_FRAMEWORK.DEFAULT_THROTTLE_RATES) so the open form can't be hammered. The
response is identical whether the address was newly added or already present, so
it never reveals who is on the list (no email enumeration).
"""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .serializers import WaitlistSignupSerializer
from .services import WaitlistService


class WaitlistSignupView(APIView):
    """POST /v1/waitlist, add an email to the waitlist (idempotent)."""

    permission_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "waitlist"

    def post(self, request):
        serializer = WaitlistSignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        signup, _created = WaitlistService.signup(
            email=serializer.validated_data["email"],
            source=serializer.validated_data.get("source", ""),
        )
        return Response({"email": signup.email}, status=status.HTTP_200_OK)
