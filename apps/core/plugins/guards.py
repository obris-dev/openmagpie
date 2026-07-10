"""Fail-loud guards + parsers for the plugin settings in conf.settings.base.

Extracted so the extra-app conflict/duplicate checks and the entry-point
allowlist parse are unit-testable without importing the Django settings module,
which runs its guards once at process start and can't be re-exercised with
different env from a test.

Import-light on purpose: this is imported at settings-eval time, so it must NOT
touch the app registry or import models (that would break settings loading).
"""

from __future__ import annotations

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
