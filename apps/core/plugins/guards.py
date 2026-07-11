"""Fail-loud guards + parsers for the plugin settings in conf.settings.base.

Extracted so the extra-app conflict/duplicate checks and the entry-point
allowlist parse are unit-testable without importing the Django settings module,
which runs its guards once at process start and can't be re-exercised with
different env from a test.

Import-light on purpose: this is imported at settings-eval time, so it must NOT
touch the app registry or import models (that would break settings loading).
"""

from __future__ import annotations

import keyword

from django.core.exceptions import ImproperlyConfigured

from common.env import split_csv


def _app_label(app: str) -> str:
    """The default app label Django derives from an entry: the last path segment
    (`django.contrib.auth` -> `auth`). Collisions are on the LABEL, not the path,
    so we compare on this. An AppConfig.label override can't be seen from a string,
    so this is approximate both ways: it can miss a collision an override would
    create (false negative), and it can flag a dotted path whose overridden label
    would NOT actually collide (false positive). Both are acceptable here because
    every installed app's real label equals its derived segment; the check catches
    the common `auth` vs `django.contrib.auth` case."""
    return app.rsplit(".", 1)[-1]


def resolve_extra_apps(extra: list[str], installed: list[str]) -> list[str]:
    """Validate OPENMAGPIE_EXTRA_APPS against the already-installed apps.

    Raises ImproperlyConfigured if an entry's app LABEL collides with an installed
    app (built-in Django, third-party, or local), which would shadow it, or is a
    duplicate within the list itself, which Django later rejects with a generic
    "application labels aren't unique". Returns `extra` unchanged when clean.
    """
    installed_labels = {_app_label(a) for a in installed}
    conflicts = [a for a in extra if _app_label(a) in installed_labels]
    if conflicts:
        raise ImproperlyConfigured(f"OPENMAGPIE_EXTRA_APPS entries conflict with installed apps: {conflicts}")
    labels = [_app_label(a) for a in extra]
    dupes = sorted({a for a in extra if labels.count(_app_label(a)) > 1})
    if dupes:
        raise ImproperlyConfigured(f"OPENMAGPIE_EXTRA_APPS contains duplicate entries: {dupes}")
    return extra


def resolve_entrypoint_allow(raw: str | None, *, default_when_unset: list[str] | None) -> list[str] | None:
    """Parse OPENMAGPIE_PLUGIN_ALLOW into the entry-point allowlist.

    Returns `default_when_unset` when the var is unset OR empty/whitespace-only
    (None means load every installed plugin, an empty list means load none):
    setting a var to "" reads as "not really set", so it falls through to the
    env default rather than silently disabling every installed plugin. A
    non-empty value returns its parsed, whitespace-stripped entry-point names.
    """
    names = split_csv(raw or "")
    if names:
        return names
    return default_when_unset


def resolve_plugin_api_urls(raw: str | None) -> list[str]:
    """Parse OPENMAGPIE_PLUGIN_API_URLS into a list of urlconf MODULE paths.

    Each comma-separated entry is a dotted module path (e.g. `myfork.urls`), like
    the other OPENMAGPIE_PLUGIN_* module references. conf.urls includes each UNDER
    the API version prefix, so the fork writes version-relative routes (no repeated
    or hardcoded `v1`) and adds REST endpoints with zero core edits. This only validates
    the dotted-path SYNTAX (each segment a non-keyword identifier): a malformed entry
    fails loud here at boot, but a well-formed path to a module that doesn't exist still
    surfaces as a bare ModuleNotFoundError at include() time. Unset -> []."""
    modules: list[str] = []
    for entry in split_csv(raw or ""):
        # Every dot-separated segment must be a non-keyword Python identifier. This
        # rejects a path/assignment/empty-segment AND embedded whitespace or
        # separators (tab, newline, `;`) that a "contains a space" check would miss
        # (split_csv only strips the edges); the keyword check keeps the friendly
        # boot error tight (a segment like `import` is a valid identifier lexically
        # but can never name a real module).
        segments = entry.split(".")
        if not all(seg.isidentifier() and not keyword.iskeyword(seg) for seg in segments):
            raise ImproperlyConfigured(
                f"OPENMAGPIE_PLUGIN_API_URLS entry {entry!r} must be a dotted urlconf module path (e.g. 'myfork.urls')"
            )
        modules.append(entry)
    # A repeated module double-includes (the second copy silently dead-routes), so
    # reject it as a config typo. Overlapping ROUTE paths across modules can't be
    # detected here (they're regexes); Django resolves first-match-wins and core's
    # patterns are listed before these, so a core route that MATCHES is served by core.
    # It's not absolute, though: Django backtracks past a core include that 404s
    # internally, so a sub-path core doesn't serve can fall through to a plugin (see
    # conf/urls.py). A plugin can't take over a route core actually serves.
    dupes = sorted({m for m in modules if modules.count(m) > 1})
    if dupes:
        raise ImproperlyConfigured(f"OPENMAGPIE_PLUGIN_API_URLS contains duplicate module(s): {dupes}")
    return modules
