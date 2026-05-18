"""Emit the JSON-Schema bundle the CLI codegens its typed models from.

This is the publish step of "server owns the schema, clients consume":
the canonical Pydantic models (`wire.ListenerWire` for the transport
shell, and each registered kind's config from `listeners.registry`)
are dumped as JSON Schema. The CLI's `make cli-types` feeds this to
datamodel-code-generator. The shapes are declared once, server-side;
the client never hand-copies them.

`configs` is published SEPARATELY from `wire` (two layers): `wire.data`
stays an opaque object (round-trip safe for unknown kinds); the
per-kind config schemas drive the additive typed construction/discovery
factory only. `config_titles` maps each kind to its schema title so the
codegen step can build `CONFIG_BY_KIND` deterministically without
hand-maintaining a kind list.
"""

import json
from typing import Any

from django.core.management.base import BaseCommand
from listeners import registry
from listeners.wire import ListenerWire


class Command(BaseCommand):
    help = "Print the wire JSON-Schema bundle (server -> CLI codegen source)."

    def handle(self, *args: Any, **options: Any) -> None:
        kinds = registry.kinds()
        bundle = {
            "wire": ListenerWire.model_json_schema(),
            "configs": {kind: cls.model_json_schema() for kind, cls in kinds.items()},
            "config_titles": {
                kind: cls.model_json_schema().get("title", cls.__name__)
                for kind, cls in kinds.items()
            },
        }
        self.stdout.write(json.dumps(bundle, indent=2))
