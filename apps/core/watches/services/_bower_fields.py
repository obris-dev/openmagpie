"""The item fields a watch can send to OpenBower: one column per scalar field
its sources produce, each with the OpenBower type its values fit, narrowed to
the fields a connect names."""

from __future__ import annotations

import types
import typing
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from openmagpie_schema.watch_enums import BowerColumnType

from ._bower_errors import BowerConnectError

# Item fields that are never a column: the row's own id rides as its
# external_id, and the rest name the item's plumbing, not its content.
_NOT_COLUMNS = frozenset({"kind", "external_id", "source", "parent_external_id"})
# String fields holding an absolute link, so OpenBower renders them as one.
# Reddit's `permalink` is a path (`/r/.../comments/...`), so it stays text;
# the item's `url` carries the same post as an absolute link.
_URL_FIELDS = frozenset({"url", "external_url", "feed_url"})
# The fields every source shares lead, in this order; each source's own follow.
LEADING_FIELDS = ("title", "content", "url", "external_url", "author", "occurred_at")


@dataclass(frozen=True)
class DerivedColumn:
    field: str
    label: str
    type: str


@dataclass(frozen=True)
class ChosenColumns:
    """The columns a connect sends, and the item fields it leaves out."""

    columns: list[DerivedColumn]
    left_out: list[str]


def choose(derived: list[DerivedColumn], names: list[str]) -> ChosenColumns:
    """The derived columns for `names`, in that order; every column when
    `names` is empty. A name the watch's sources do not produce is refused,
    naming the ones they do."""
    if not names:
        return ChosenColumns(columns=derived, left_out=[])
    by_field = {column.field: column for column in derived}
    unknown = [name for name in names if name not in by_field]
    if unknown:
        raise BowerConnectError(
            f"the watch's items have no field {', '.join(unknown)}; "
            f"its fields are {', '.join(column.field for column in derived)}"
        )
    chosen = set(names)
    left_out = [column.field for column in derived if column.field not in chosen]
    return ChosenColumns(columns=[by_field[name] for name in names], left_out=left_out)


def scalar_fields(payload: type[BaseModel]) -> dict[str, str]:
    """Each field a payload class declares that a cell can hold, with its
    column type; lists, objects, and the plumbing fields are left out."""
    fields: dict[str, str] = {}
    for name, info in payload.model_fields.items():
        if name in _NOT_COLUMNS:
            continue
        column_type = _column_type(name, info.annotation)
        if column_type is not None:
            fields[name] = column_type
    return fields


def field_label(field: str) -> str:
    """`external_url` -> `External url`."""
    words = field.replace("_", " ")
    return words[:1].upper() + words[1:]


def _column_type(name: str, annotation: Any) -> str | None:
    # Every value Magpie sends must fit its column's type, or OpenBower
    # refuses the whole push, so only an integer is a `number`, and a
    # timestamp is a `datetime` (an item's timestamps are UTC-aware, dumped
    # with their offset).
    base = _without_none(annotation)
    if base is bool:
        return BowerColumnType.TEXT
    if base is int:
        return BowerColumnType.NUMBER
    if base is datetime:
        return BowerColumnType.DATETIME
    if base is str:
        return BowerColumnType.URL if name in _URL_FIELDS else BowerColumnType.TEXT
    return None


def _without_none(annotation: Any) -> Any:
    """`X | None` -> `X`; anything else unchanged."""
    if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
        members = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation
