"""DRF serializers for the public waitlist API."""

from rest_framework import serializers

from .constants import WaitlistSourceInterest


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
    # Multi-select source vote. Omitted on the initial email POST (defaults to
    # []) and sent on the confirmation card's submit. Each element must be a
    # valid source (ChoiceField rejects anything else); an empty list is a
    # no-op server-side (the service only records a non-empty vote).
    source_interests = serializers.ListField(
        child=serializers.ChoiceField(choices=[c.value for c in WaitlistSourceInterest]),
        required=False,
        default=list,
        # At most one of each source; rejects an oversized list cheaply (before
        # per-element validation + de-dup). Throttling is the real DoS guard.
        max_length=len(WaitlistSourceInterest),
    )
    # Free text paired with "other" among source_interests. Ignored otherwise.
    source_interest_other = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=120,
        default="",
    )
