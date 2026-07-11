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
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

from pydantic.json_schema import models_json_schema

from . import guards
from .guards import DEFAULT_DISCOVERY_PACKAGES
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


def _defs_for_mode(mode: Mode, input_models: Sequence[type[Any]] = INPUT_MODELS) -> dict[str, Any]:
    """The `$defs` bundle over `input_models` and everything they reference, in one mode."""
    _, bundle = models_json_schema([(model, mode) for model in input_models], ref_template=REF_TEMPLATE)
    return bundle.get(DEFS_KEY, {})


def build_schema(contract_models: Sequence[type[Any]] = CONTRACT_MODELS) -> dict[str, Any]:
    """Build the one bundled JSON Schema document over `contract_models`.

    Every model is keyed by its class name under `$defs`. The roots are listed
    under `x-roots` as an ANNOTATION, not a `oneOf`/`anyOf`: this document is a
    `$defs` library, not a schema a single instance validates against. Consumers
    generate from `$defs` and read `x-roots` to find the top-level shapes."""
    keyed = [(model, SERIALIZATION_MODE) for model in contract_models]
    # `refs` is Pydantic's authoritative (model, mode) -> $ref map; use it rather
    # than rebuilding the ref by hand, so if two models ever share a class name
    # across modules (Pydantic disambiguates the key) the roots still resolve.
    refs, bundle = models_json_schema(keyed, ref_template=REF_TEMPLATE)
    return {
        "$schema": DIALECT,
        "title": "OpenMagpie contract",
        DEFS_KEY: bundle.get(DEFS_KEY, {}),
        "x-roots": [refs[(model, SERIALIZATION_MODE)] for model in contract_models],
    }


def guard_failure(
    schema: dict[str, Any],
    *,
    input_models: Sequence[type[Any]] = INPUT_MODELS,
    excluded: frozenset[str] = EXCLUDED_MODELS,
    discovery_packages: Sequence[ModuleType] = DEFAULT_DISCOVERY_PACKAGES,
) -> str | None:
    """First failing coverage guard as a message, or None if all pass.

    Order matters: unique names underpin the name-keyed completeness check, so
    that runs first. `discovery_packages` is what the completeness / stale guards
    walk; a FORK passes its own schema package alongside `openmagpie_schema`."""
    discovered = guards.discovered_models(discovery_packages)
    names = {name for name, _ in discovered}

    dupes = guards.duplicate_model_names(discovered)
    if dupes:
        return (
            f"model name(s) defined in multiple modules: {', '.join(dupes)}.\n"
            "The schema keys coverage by class name; keep model names unique."
        )
    missing = guards.unaccounted_models(set(schema.get(DEFS_KEY, {})), excluded, names)
    if missing:
        return (
            f"{len(missing)} model(s) neither in the schema nor EXCLUDED_MODELS: {', '.join(missing)}.\n"
            "Add each to CONTRACT_MODELS, or to EXCLUDED_MODELS with a reason."
        )
    stale = guards.stale_exclusions(excluded, names)
    if stale:
        return f"EXCLUDED_MODELS names {len(stale)} model(s) that no longer exist: {', '.join(stale)}. Remove them."
    diverged = guards.property_divergences(
        _defs_for_mode(VALIDATION_MODE, input_models), _defs_for_mode(SERIALIZATION_MODE, input_models)
    )
    if diverged:
        return (
            f"input model(s) differ between validation and serialization mode: {', '.join(diverged)}.\n"
            "An alias or computed field means the serialization-mode request schema no longer matches\n"
            "what the server validates. Emit these in validation mode, or drop the asymmetry."
        )
    return None


def render(
    contract_models: Sequence[type[Any]] = CONTRACT_MODELS,
    *,
    input_models: Sequence[type[Any]] = INPUT_MODELS,
    excluded: frozenset[str] = EXCLUDED_MODELS,
    discovery_packages: Sequence[ModuleType] = DEFAULT_DISCOVERY_PACKAGES,
) -> tuple[str, str | None]:
    """Render the deterministic schema JSON for `contract_models` and run the
    coverage guards; return `(rendered_json, failure_or_none)`.

    Parameterized so a FORK can produce its own SUPERSET contract from its own
    model lists (import these, extend the core lists, and write to its own path
    via `write_or_check`) with zero edits to this module. `discovery_packages` is
    what the completeness / stale-exclusion guards walk: a fork passes its own
    schema package alongside `openmagpie_schema` so its models get the same
    coverage. Core calls it with the defaults, so `schema.json` stays
    byte-identical and deterministic.

    A fork's `excluded` must be a SUPERSET of core's EXCLUDED_MODELS (extend, don't
    replace): the completeness guard walks `discovery_packages` (which includes
    openmagpie_schema), so a core model that core deliberately excludes is discovered on
    the fork run too and false-fails the guard as "unaccounted" unless the fork carries
    core's exclusions forward. Same for `contract_models` / `input_models`: extend the
    core lists, don't supply a minimal fork-only list."""
    schema = build_schema(contract_models)
    failure = guard_failure(schema, input_models=input_models, excluded=excluded, discovery_packages=discovery_packages)
    if failure:
        return "", failure  # skip serialization; the caller surfaces the failure and stops
    return json.dumps(schema, indent=2, sort_keys=True) + "\n", None


def write_or_check(rendered: str, out_path: Path, *, check: bool) -> int:
    """Write `rendered` to `out_path`, or (with `check`) fail on drift. Returns an
    exit code. Shared by the core CLI and any fork generator.

    `rendered` must be a real schema document. `render()` returns `("", failure)` on a
    guard failure; a caller that forwards that `""` here (instead of stopping on the
    failure, as `main` does) would otherwise get a misleading "stale" in check mode or a
    raw JSONDecodeError in write mode. Reject it up front with a named, explanatory
    error so the true cause (an empty/invalid render) is what surfaces."""
    if not rendered.strip():
        raise ValueError(
            "write_or_check got an empty render: render() returns ('', failure) when a coverage guard "
            "fails. Check render()'s second return value and surface that failure instead of writing/"
            "checking the empty string."
        )
    if check:
        # Content comparison, deliberately EOL-agnostic: read_text normalizes
        # line endings, so a stray CRLF in a working tree (or a Windows checkout)
        # doesn't false-fail. Line endings in the JSON are cosmetic (the consumer
        # parses it), so the guard polices CONTENT, not bytes.
        current = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
        if current != rendered:
            # The core command is LABELED as such (not the bare default) so a core dev
            # has it while a fork, calling this with its OWN out_path, isn't misdirected.
            sys.stderr.write(
                f"{out_path} is stale. Re-run the schema generator (core: `make local-schema`) and commit.\n"
            )
            return 1
        return 0
    if not out_path.parent.is_dir():
        # Named error instead of a raw FileNotFoundError stack (e.g. a fork's
        # OPENMAGPIE_SCHEMA_JSON pointing into a not-yet-created directory). Mirrors the
        # empty-render guard above and the web generator's output-dir guard: fail loud
        # with the cause. Deliberately does NOT mkdir: creating a tree for a path the
        # caller likely mistyped would write the artifact somewhere they don't expect.
        raise FileNotFoundError(
            f"output directory does not exist: {out_path.parent}. Create it or fix the output path."
        )
    # newline="\n" so a regeneration on Windows can't write CRLF into the artifact.
    out_path.write_text(rendered, encoding="utf-8", newline="\n")
    # Report the $defs (model) count, not a line count: a stronger sanity signal that
    # the expected number of models was emitted.
    model_count = len(json.loads(rendered).get(DEFS_KEY, {}))
    sys.stderr.write(f"Wrote {out_path} ({model_count} models).\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate / verify the shared cross-boundary JSON Schema.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare the committed schema to a fresh render and exit non-zero on drift.",
    )
    args = parser.parse_args(argv)

    rendered, failure = render()
    if failure:
        sys.stderr.write(failure + "\n")
        return 1
    return write_or_check(rendered, SCHEMA_PATH, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
