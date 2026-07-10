"""Load self-registering plugin hooks at startup.

A *hook* is a zero-arg callable that registers its kind(s) into the appropriate
registry (e.g. `watches.actions.registry.register(...)`). Hooks come from two
sources, both resolved here:

- `paths`: "module:function" import strings from settings (`PLUGIN_HOOKS`, env
  `OPENMAGPIE_PLUGIN_HOOKS`). The no-packaging path -- a fork points an env var at
  an in-repo hook; nothing is published or installed.
- `entry_group`: `importlib.metadata` entry points in a group (`openmagpie.plugins`),
  filtered by `allow` (the hosted allowlist). The distribution path -- a
  pip-installed package advertises the hook.

A broken hook is logged and skipped: plugin loading is a resilience boundary, so
one bad plugin must never stop the app from booting.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from importlib import import_module, metadata

logger = logging.getLogger("plugins")

Hook = Callable[[], object]


def load_hooks(paths: Iterable[str], *, entry_group: str, allow: Iterable[str] | None = None) -> list[str]:
    """Invoke every resolvable hook from `paths` then `entry_group`; return the labels that ran.
    Never raises on a plugin's behalf -- failures are logged and skipped."""
    loaded: list[str] = []
    for path in paths:
        hook = _resolve_path(path)
        if hook is not None and _invoke(hook, label=path):
            loaded.append(path)
    for name, hook in _entry_point_hooks(entry_group, allow):
        if _invoke(hook, label=f"{entry_group}:{name}"):
            loaded.append(name)
    return loaded


def _resolve_path(path: str) -> Hook | None:
    """'pkg.module:function' -> the callable, or None (logged) when unresolvable."""
    module_name, sep, attr = path.partition(":")
    if not sep or not module_name or not attr:
        logger.warning("plugin hook path %r is not 'module:function'; skipping", path)
        return None
    try:
        return getattr(import_module(module_name), attr)
    except (ImportError, AttributeError) as exc:
        logger.warning("plugin hook %r failed to import (%s); skipping", path, exc)
        return None


def _entry_point_hooks(group: str, allow: Iterable[str] | None) -> Iterator[tuple[str, Hook]]:
    """Yield (name, hook) for each entry point in `group`, filtered by `allow` (None = load all)."""
    allowed = None if allow is None else set(allow)
    for ep in metadata.entry_points(group=group):
        if allowed is not None and ep.name not in allowed:
            logger.info("plugin %r not in allowlist; skipping", ep.name)
            continue
        try:
            yield ep.name, ep.load()
        except Exception as exc:  # untrusted plugin load boundary; a bad plugin must not break boot
            logger.warning("plugin entry point %r failed to load (%s); skipping", ep.name, exc)


def _invoke(hook: Hook, *, label: str) -> bool:
    """Call a hook; log+swallow any failure so a bad plugin can't stop startup."""
    try:
        hook()
    except Exception as exc:  # untrusted plugin boundary; a bad plugin must not break boot
        logger.warning("plugin hook %s raised (%s); skipping", label, exc)
        return False
    logger.info("loaded plugin hook %s", label)
    return True
