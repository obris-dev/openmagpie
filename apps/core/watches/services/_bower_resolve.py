"""What a bower connect resolves a request to before adding the action: the
signal, its columns, and the column key -> item field map. A new signal
resolves when it is created (in BowerConnectService); an existing one, and a
dry run's would-be new one, resolve here."""

from __future__ import annotations

from dataclasses import dataclass

from openmagpie_schema.watch import BowerConnectColumn, BowerConnectExisting

from ._bower_errors import BowerConnectError
from ._bower_fields import ChosenColumns, DerivedColumn
from ._bower_signal import SignalMatch, match, read_columns, signal_id_of


@dataclass(frozen=True)
class ResolvedSignal:
    """What either route resolves to before the action is added: the signal
    (no id and no column keys on a dry run that would create it), its
    columns, the action's column key -> item field map, and the chosen item
    fields no column takes. `created` is whether this connect made it."""

    signal_id: str | None
    label: str
    url: str
    columns: list[BowerConnectColumn]
    column_map: dict[str, str]
    unmatched_fields: list[str]
    created: bool

    def not_added(self, reason: str) -> str:
        if not self.created:
            return f"The action was not added ({reason})."
        return (
            f"The signal {self.label!r} ({self.signal_id}) was created in OpenBower, but the action was not "
            f"added ({reason}). Connect it with its webhook URL, copied from OpenBower."
        )


def planned(label: str, chosen: ChosenColumns) -> ResolvedSignal:
    """A new signal as a dry run shows it: no id and no column keys yet."""
    columns = [BowerConnectColumn(label=c.label, type=c.type, field=c.field) for c in chosen.columns]
    return ResolvedSignal(
        signal_id=None, label=label, url="", columns=columns, column_map={}, unmatched_fields=[], created=False
    )


def existing(request: BowerConnectExisting, chosen: ChosenColumns) -> ResolvedSignal:
    """An existing signal: its id from the URL, its columns read and matched."""
    signal_id = _signal_id(request.webhook_url)
    matched = _match_existing(request, chosen.columns)
    return ResolvedSignal(
        signal_id=signal_id,
        label="",
        url=request.webhook_url,
        columns=matched.columns,
        column_map=matched.column_map,
        unmatched_fields=matched.unmatched_fields,
        created=False,
    )


def _match_existing(request: BowerConnectExisting, columns: list[DerivedColumn]) -> SignalMatch:
    """The existing signal's columns, matched to the chosen item fields;
    refused when no column takes any field, or when a field the request
    named outright has no column to take it (it would never be sent)."""
    matched = match(read_columns(request.webhook_url), [(c.field, c.label, c.type) for c in columns])
    if not matched.column_map:
        raise BowerConnectError(
            "none of the signal's columns matches an item field by name, so no row would carry a value"
        )
    if request.fields and matched.unmatched_fields:
        raise BowerConnectError(
            f"no column of the signal takes {', '.join(matched.unmatched_fields)}; "
            "add a column with that name in OpenBower, or leave the field out"
        )
    return matched


def _signal_id(webhook_url: str) -> str:
    signal_id = signal_id_of(webhook_url)
    if signal_id is None:
        raise BowerConnectError(
            "webhook_url is not a signal's webhook URL; copy it from the signal's page in OpenBower"
        )
    return signal_id
