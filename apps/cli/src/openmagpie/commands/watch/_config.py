"""Config normalizers for `magpie watch` actions, shared by the read AND write paths.

Split out of `_render` (whose display-oriented name undersold them): these are pure
config helpers used by BOTH the detail renderer (read) and the edit-seed builders
(write), so a config-named home reads truer than a rendering one. No network or file IO,
no command logic: pure functions over a wire config. (Union error RENDERING moved to the
shared `_shared.errors` home, beside its server-side twin.)"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ...api.watch import WatchActionWire


def _config_json(config: BaseModel | dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a watch action's wire `config` to a JSON-ready dict (or None).

    A built-in kind's `config` is a typed per-kind model; a plugin (non-built-in)
    kind's is a plain dict, the `PluginActionWire` fallback member. Dump a model, pass
    a dict/None through. The bare `config.model_dump(...)` call sites crash with
    AttributeError on a plugin kind's dict without this."""
    if isinstance(config, BaseModel):
        return config.model_dump(mode="json")
    return config


def _edit_seed_config(wire: WatchActionWire) -> tuple[dict[str, Any], bool]:
    """The `config` dict for an edit seed, plus whether the stored config was CORRUPT.

    A corrupt-at-rest config degrades to null on the wire (`config=None`); seed an empty
    `{}` placeholder rather than crash on the None (or feed None to
    build_watch_action_input, which needs a dict). Otherwise the normalized config. The
    single-action (`_actions`) and whole-watch (`_crud`) edit-seed builders both call
    this, so the corrupt-degrade rule lives in ONE place; each uses the `corrupt` flag
    for its own presentation (a YAML NOTE vs a placeholder dict shape)."""
    config = wire.config
    if config is None:
        return {}, True
    # Non-None here, so the result is always a dict (no dead `or {}` fallback needed):
    # dump a typed model, pass a plugin kind's dict through (the _config_json rule).
    return (config.model_dump(mode="json") if isinstance(config, BaseModel) else config), False
