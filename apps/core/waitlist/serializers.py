"""DRF serializers for the public waitlist API."""

from rest_framework import serializers

from .constants import WaitlistCategory


class _EmailField(serializers.EmailField):
    """Lowercased, stripped email. Matches the service's normalize step."""

    def to_internal_value(self, data):
        return super().to_internal_value(data).strip().lower()


class WaitlistSignupSerializer(serializers.Serializer):
    email = _EmailField()
    # Free-form provenance (e.g. the marketing form id "hero" / "cta"). Optional.
    source = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=64,
        default="",
    )
    # Which offering they're waiting for. Omitted on the initial email POST
    # (defaults to UNKNOWN) and sent on the confirmation card's second POST.
    # ChoiceField rejects anything off the enum; UNKNOWN is accepted but is a
    # no-op server-side (the service only records a real pick).
    category = serializers.ChoiceField(
        choices=[c.value for c in WaitlistCategory],
        required=False,
        default=WaitlistCategory.UNKNOWN.value,
    )
