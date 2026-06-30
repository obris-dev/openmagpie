"""Emit the cross-boundary schema as one bundled JSON Schema, and guard it.

`openmagpie_schema` is the single source of truth for the wire shapes shared by
core and the magpie CLI. This tool projects the public request / response /
config models (declared in models.py) into a single JSON Schema document
(2020-12, one shared `$defs` block) so a non-Python consumer (the web client)
can generate its own validators from the same definitions instead of
hand-maintaining a parallel copy.

It lives under tools/ rather than inside the package on purpose: it's an
executable generator that reflects over the models, not a wire shape itself,
and a repo script's path is stable (an in-package module breaks when the
package is installed non-editable and `__file__` moves into site-packages).

    uv run --no-sync python -m tools.schema_sync.generate            # write schema.json
    uv run --no-sync python -m tools.schema_sync.generate --check    # fail if stale

`--check` is the drift guard: regenerate in memory and compare (EOL-agnostic) to
the committed file, the same discipline as `uv lock --locked`. Both modes also
run guards that keep coverage sound: unique model names, no package model
silently omitted, no stale exclusion, and request/response mode parity.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic.json_schema import models_json_schema

from . import guards
from .models import CONTRACT_MODELS, EXCLUDED_MODELS, INPUT_MODELS

# The committed artifact, in the schema package it belongs to. Computed relative
# to THIS script (tools/schema_sync/generate.py -> repo root), which is stable:
# a repo script is never installed, so its path can't move into site-packages.
REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "packages" / "openmagpie-schema" / "schema.json"

# JSON Schema dialect Pydantic v2 targets; pinned so a generator picks the right parser.
DIALECT = "https://json-schema.org/draft/2020-12/schema"
DEFS_KEY = "$defs"
REF_TEMPLATE = f"#/{DEFS_KEY}/{{model}}"

# Pydantic emits a per-mode schema. "serialization" is the shape the server
# writes on the wire (what a client validates a response against); the schema is
# emitted in this mode. "validation" is the shape the server accepts; the
# mode-parity guard uses it to confirm the same schema is correct for requests.
Mode = Literal["validation", "serialization"]
SERIALIZATION_MODE: Mode = "serialization"
VALIDATION_MODE: Mode = "validation"


def _defs_for_mode(mode: Mode) -> dict[str, Any]:
    """The `$defs` bundle over INPUT_MODELS and everything they reference, in one mode."""
    _, bundle = models_json_schema([(model, mode) for model in INPUT_MODELS], ref_template=REF_TEMPLATE)
    return bundle.get(DEFS_KEY, {})


def build_schema() -> dict[str, Any]:
    """Build the one bundled JSON Schema document over CONTRACT_MODELS.

    Every model is keyed by its class name under `$defs`. The roots are listed
    under `x-roots` as an ANNOTATION, not a `oneOf`/`anyOf`: this document is a
    `$defs` library, not a schema a single instance validates against. Consumers
    generate from `$defs` and read `x-roots` to find the top-level shapes."""
    keyed = [(model, SERIALIZATION_MODE) for model in CONTRACT_MODELS]
    # `refs` is Pydantic's authoritative (model, mode) -> $ref map; use it rather
    # than rebuilding the ref by hand, so if two models ever share a class name
    # across modules (Pydantic disambiguates the key) the roots still resolve.
    refs, bundle = models_json_schema(keyed, ref_template=REF_TEMPLATE)
    return {
        "$schema": DIALECT,
        "title": "OpenMagpie contract",
        DEFS_KEY: bundle.get(DEFS_KEY, {}),
        "x-roots": [refs[(model, SERIALIZATION_MODE)] for model in CONTRACT_MODELS],
    }


def guard_failure(schema: dict[str, Any]) -> str | None:
    """First failing coverage guard as a message, or None if all pass.

    Order matters: unique names underpin the name-keyed completeness check, so
    that runs first."""
    discovered = guards.discovered_models()
    names = {name for name, _ in discovered}

    dupes = guards.duplicate_model_names(discovered)
    if dupes:
        return (
            f"model name(s) defined in multiple modules: {', '.join(dupes)}.\n"
            "The schema keys coverage by class name; keep model names unique."
        )
    missing = guards.unaccounted_models(set(schema.get(DEFS_KEY, {})), EXCLUDED_MODELS, names)
    if missing:
        return (
            f"{len(missing)} model(s) neither in the schema nor EXCLUDED_MODELS: {', '.join(missing)}.\n"
            "Add each to CONTRACT_MODELS, or to EXCLUDED_MODELS with a reason."
        )
    stale = guards.stale_exclusions(EXCLUDED_MODELS, names)
    if stale:
        return f"EXCLUDED_MODELS names {len(stale)} model(s) that no longer exist: {', '.join(stale)}. Remove them."
    diverged = guards.property_divergences(_defs_for_mode(VALIDATION_MODE), _defs_for_mode(SERIALIZATION_MODE))
    if diverged:
        return (
            f"input model(s) differ between validation and serialization mode: {', '.join(diverged)}.\n"
            "An alias or computed field means the serialization-mode request schema no longer matches\n"
            "what the server validates. Emit these in validation mode, or drop the asymmetry."
        )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate / verify the shared cross-boundary JSON Schema.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare the committed schema to a fresh render and exit non-zero on drift.",
    )
    args = parser.parse_args(argv)

    schema = build_schema()
    failure = guard_failure(schema)
    if failure:
        sys.stderr.write(failure + "\n")
        return 1

    rendered = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    if args.check:
        # Content comparison, deliberately EOL-agnostic: read_text normalizes
        # line endings, so a stray CRLF in a working tree (or a Windows checkout)
        # doesn't false-fail. Line endings in the JSON are cosmetic (the consumer
        # parses it), so the guard polices CONTENT, not bytes.
        current = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else ""
        if current != rendered:
            sys.stderr.write(f"{SCHEMA_PATH.name} is stale. Run `python -m tools.schema_sync.generate` and commit.\n")
            return 1
        return 0
    # newline="\n" so a regeneration on Windows can't write CRLF into the artifact.
    SCHEMA_PATH.write_text(rendered, encoding="utf-8", newline="\n")
    sys.stderr.write(f"Wrote {SCHEMA_PATH} ({len(CONTRACT_MODELS)} root models).\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
