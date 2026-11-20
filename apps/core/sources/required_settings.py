"""Server settings a connector needs before its sources can poll.

A connector declares them as `required_settings`, a map of setting name to a
short note on where the value comes from (documented on the `Connector` Protocol;
`BaseConnector` defaults it to none). Feed writes refuse a source whose
connector is missing one, and the poller refuses to poll it, both with the same
message (`refusal`): how to set it and get the server to load it, which each
settings profile words as `ENV_RELOAD_HINT`, since a running server reads its
environment only at start.
"""

from typing import TYPE_CHECKING, Any

from django.conf import settings

if TYPE_CHECKING:
    # Type-only: the connectors package imports the connectors, which import this.
    from sources.connectors.base import Connector


def missing_settings(connector: "Connector[Any]") -> dict[str, str]:
    """The connector's required settings that are empty on this server, each
    with its where-to-get-it note. `getattr` because the attribute is optional on
    the Protocol: a plugin connector that doesn't subclass BaseConnector may omit it."""
    required: dict[str, str] = getattr(connector, "required_settings", None) or {}
    return {name: note for name, note in required.items() if not getattr(settings, name, "")}


def refusal(kind: str, connector: "Connector[Any]") -> str | None:
    """Why a `kind` source can't be used on this server, naming every missing
    setting, or None when nothing is missing. The one message feed writes and the
    poll both give."""
    missing = missing_settings(connector)
    if not missing:
        return None
    return " ".join(missing_setting_message(kind, name, note) for name, note in missing.items())


def missing_setting_message(kind: str, name: str, note: str) -> str:
    return f"{kind} sources need {name} ({note}). {settings.ENV_RELOAD_HINT.format(name=name)}"
