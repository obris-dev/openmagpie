"""Listener kind → typed config class.

`Listener.data` is a JSON blob whose schema depends on `Listener.kind`. This
registry maps the kind string to its Pydantic config class for validation.
"""

from listeners.configs import ListenerConfig, SemanticListenerConfig
from listeners.models import Listener

_REGISTRY: dict[str, type[ListenerConfig]] = {
    SemanticListenerConfig.LISTENER_KIND: SemanticListenerConfig,
}


def get_config_class(kind: str) -> type[ListenerConfig]:
    return _REGISTRY[kind]


def kinds() -> dict[str, type[ListenerConfig]]:
    """All registered kind -> config class. Single source for "what
    kinds exist": consumed by `dump_wire_schema` to publish the per-kind
    config schemas the CLI codegens its typed factory from."""
    return dict(_REGISTRY)


def load_config(listener: Listener) -> ListenerConfig:
    """Validate listener.data against the kind's Pydantic config class."""
    return get_config_class(str(listener.kind)).model_validate(listener.data)
