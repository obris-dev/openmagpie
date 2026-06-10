"""SourcePayload registry: (source, PAYLOAD_KIND) -> SourcePayload subclass.

Connectors register their payload classes at import time so we can hydrate a
stored payload dump (FeedItem.data) back into a typed SourcePayload when an
action needs to judge it.
"""

from pydantic import ValidationError as _PydanticValidationError

from sources.payloads import SourcePayload

_REGISTRY: dict[tuple[str, str], type[SourcePayload]] = {}


class UnhydrateablePayload(Exception):
    """Permanent failure to reconstruct a SourcePayload from a stored dump.

    The action runner advances past / marks the row so it doesn't loop on
    the same poison item every cycle. Subclasses distinguish the cause for
    forensics ; operators read the log.
    """


class UnknownPayloadKind(UnhydrateablePayload):
    """The dump's `(source, kind)` pair isn't in the registry. A connector
    was renamed or removed; the old pair can't be reconstructed."""


class InvalidPayloadData(UnhydrateablePayload):
    """The dump's class IS registered but `model_validate` rejects it.
    Common cause: schema drift ; a new required field was added, or a
    field type changed, between when the row was written and now. The
    row can't be retried into compliance; it's permanently bad."""


def register(source: str, payload_classes: list[type[SourcePayload]]) -> None:
    """Register concrete SourcePayload classes for a source kind.

    Enforces the `sample()` override here (not at class-definition time
    via `__init_subclass__`) so connector authors can declare abstract
    intermediate bases (e.g. a shared base for post / comment payloads)
    without tripping the guard at import. Only classes that actually get
    registered are required to have a real `sample()`; intermediates pass
    through untouched.
    """
    for cls in payload_classes:
        if "sample" not in cls.__dict__:
            raise TypeError(
                f"{cls.__name__} is registered but does not override SourcePayload.sample() — "
                "payload-preview would 500 on this kind. Implement sample()."
            )
        _REGISTRY[(source, cls.PAYLOAD_KIND)] = cls


def registered() -> dict[tuple[str, str], type[SourcePayload]]:
    """A copy of the `(source, PAYLOAD_KIND) -> class` map, for callers that
    enumerate every registered payload (e.g. the schema-parity test) without
    reaching into the module-private `_REGISTRY`."""
    return dict(_REGISTRY)


def class_for_source(source: str) -> type[SourcePayload] | None:
    """First registered SourcePayload class for the given source-connector
    kind (e.g. `"reddit_subreddit"`). None if nothing is registered for
    that source. When a source has multiple payload kinds (e.g. future:
    `new_post` + `new_comment`), returns the first registered.

    TODO: ambiguous when a source ships multiple payload kinds ; preview
    would render whichever was registered first regardless of the actual
    kind. No triggering case today (one kind per source); revisit when a
    second is added (needs a design decision: take an explicit kind hint,
    or expose the choice via feed spec)."""
    for (registered_source, _kind), cls in _REGISTRY.items():
        if registered_source == source:
            return cls
    return None


def hydrate_data(data: dict) -> SourcePayload:
    """Reconstruct a typed SourcePayload from a stored payload dump.

    Reads `source` + `kind` from the dump itself (the payload carries
    both), so this works for any persisted snapshot ; a FeedItem's data.

    Raises `UnhydrateablePayload` (or one of its subclasses) on any
    permanent failure: an unknown `(source, kind)` pair, or a known pair
    whose data fails the typed model's validation. Both signal "skip this
    row forever; retrying can't help."
    """
    source = str(data.get("source"))
    kind = str(data.get("kind"))
    try:
        cls = _REGISTRY[(source, kind)]
    except KeyError as exc:
        raise UnknownPayloadKind(f"no SourcePayload class registered for (source={source!r}, kind={kind!r})") from exc
    try:
        return cls.model_validate(data)
    except _PydanticValidationError as exc:
        raise InvalidPayloadData(
            f"data fails {cls.__name__}.model_validate: {exc.error_count()} error(s); first: {exc.errors()[0]}"
        ) from exc
