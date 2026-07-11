"""Coverage / soundness guards for the shared schema.

Pure predicates: each takes what it needs and returns the offending names (empty
== sound). generate.py wires them to the registry + the built schema. Keeping
them free of the build constants makes the checks trivially testable and keeps
this module dependency-light.
"""

import importlib
import inspect
import json
import pkgutil
from collections import Counter
from collections.abc import Sequence
from types import ModuleType
from typing import Any

from pydantic import BaseModel

import openmagpie_schema

# The packages the coverage guards walk by default: just the core schema package. A
# fork passes `(openmagpie_schema, myfork_schema)`. Named once here (shared by
# discovered_models + generate.render/guard_failure) so the default isn't restated.
DEFAULT_DISCOVERY_PACKAGES: tuple[ModuleType, ...] = (openmagpie_schema,)


def _reraise(name: str) -> None:
    # walk_packages defaults to onerror=None, which SILENTLY skips a sub-package
    # whose __init__ raises (every model beneath it then vanishes from
    # discovery, defeating the completeness guard). Re-raise so a broken
    # sub-package fails loudly instead. `raise` re-raises the import error
    # walk_packages is handling when it calls this back.
    raise


def discovered_models(packages: Sequence[ModuleType] = DEFAULT_DISCOVERY_PACKAGES) -> list[tuple[str, str]]:
    """(name, defining module) for every concrete BaseModel in each package.

    Walks each package so a newly added model is discovered automatically. Keyed
    by defining module (not import site), so a re-exported class dedups while
    two DISTINCT classes sharing a name surface as two entries (a collision).

    Defaults to the core `openmagpie_schema`. A FORK passes BOTH its own schema
    package and `openmagpie_schema` (e.g. `(openmagpie_schema, myfork_schema)`) so
    the completeness + stale-exclusion guards cover its models AND the core ones it
    reuses; otherwise a fork gets no coverage of its own classes and false-positive
    stale exclusions for the core names in its excluded set."""
    found: set[tuple[str, str]] = set()
    for package in packages:
        pkg = package.__name__
        for info in pkgutil.walk_packages(package.__path__, pkg + ".", onerror=_reraise):
            module = importlib.import_module(info.name)
            for _, obj in inspect.getmembers(module, inspect.isclass):
                # `== pkg or startswith(pkg + ".")`, not `startswith(pkg)`, so a
                # sibling top-level package sharing the prefix can't leak in.
                own = obj.__module__ == pkg or obj.__module__.startswith(pkg + ".")
                if issubclass(obj, BaseModel) and obj is not BaseModel and own:
                    found.add((obj.__name__, obj.__module__))
    return sorted(found)


def duplicate_model_names(discovered: list[tuple[str, str]]) -> list[str]:
    """Names defined by two+ distinct models across modules.

    The schema keys coverage by bare class name, and Pydantic keys `$defs` by
    name (disambiguating only on collision), so a collision would make the
    completeness guard false-pass (the discovery set dedups) or false-fail (the
    bare name won't match a disambiguated `$defs` key). Fail loudly instead,
    enforcing the uniqueness the whole name-based scheme rests on."""
    counts = Counter(name for name, _ in discovered)
    return sorted(name for name, count in counts.items() if count > 1)


def unaccounted_models(defs_keys: set[str], excluded: frozenset[str], discovered_names: set[str]) -> list[str]:
    """Package models that are neither emitted into the schema nor excluded.

    A non-empty result is a drift the completeness guard rejects: someone added
    a wire model and forgot to either expose it or record why it's left out."""
    return sorted(discovered_names - (defs_keys | excluded))


def stale_exclusions(excluded: frozenset[str], discovered_names: set[str]) -> list[str]:
    """EXCLUDED_MODELS entries that no longer name a real package model.

    Names (not classes) are used for exclusions, so a deleted or renamed model
    would otherwise leave a dead entry forever. This surfaces the removal."""
    return sorted(excluded - discovered_names)


def property_divergences(validation_defs: dict[str, Any], serialization_defs: dict[str, Any]) -> list[str]:
    """Defs whose per-field schema differs between the two modes.

    Given the `$defs` bundles built over the input-model closure in validation
    and serialization mode, flag any def whose whole `properties` object differs
    (a renaming alias, a computed field, or a serializer that reshapes a value).
    `required` lives at the model level, not in `properties`, so the benign
    "defaulted field optional on input, present on output" difference is ignored.
    A non-empty result means a request schema would no longer match what the
    server validates."""
    diverged = []
    for name in set(validation_defs) | set(serialization_defs):
        v = validation_defs.get(name, {}).get("properties", {})
        s = serialization_defs.get(name, {}).get("properties", {})
        if json.dumps(v, sort_keys=True) != json.dumps(s, sort_keys=True):
            diverged.append(name)
    return sorted(diverged)
