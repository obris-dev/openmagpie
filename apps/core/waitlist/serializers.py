"""DRF serializers for the public waitlist API."""

from rest_framework import serializers


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
