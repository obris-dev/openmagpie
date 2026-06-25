"""Action-implementation registry: kind -> runnable `Action` instance.

The EXECUTION-layer registry, distinct from `watches.registry` (the
CONFIG-layer kind -> Pydantic config class). The drain looks up the impl
for a run's `action.kind` here and calls `.run(...)`. A new action kind
registers its impl here ; same shape as `engine.registry` /
`sources.registry`.
"""

from .extract import ExtractAction
from .log import LogAction
from .protocol import Action
from .semantic_filter import SemanticFilterAction
from .webhook import WebhookAction

_REGISTRY: dict[str, Action] = {
    SemanticFilterAction.kind: SemanticFilterAction(),
    ExtractAction.kind: ExtractAction(),
    WebhookAction.kind: WebhookAction(),
    LogAction.kind: LogAction(),
}


def get(kind: str) -> Action:
    """The runnable Action for `kind`. Raises KeyError if no impl is
    registered (a kind that validates as config but has no executor yet ;
    the drain treats that as a permanent ERROR on the run)."""
    return _REGISTRY[kind]


def register(action: Action) -> None:
    _REGISTRY[action.kind] = action
