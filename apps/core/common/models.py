from django.db import models
from django.utils.translation import gettext_lazy as _

from openmagpie_schema import KIND_MAX_LENGTH

from .fields import ULIDField

# KIND_MAX_LENGTH is the bound of every `kind` discriminator column (WatchAction.kind,
# WatchActionRun.kind, WatchActionBackfill.kind, Source.kind) and the plugin register
# facades, so a fork's over-long kind fails at boot, not with a write-time DataError
# (notably on the run-enqueue path). It's OWNED by the wire-contract package
# (openmagpie_schema) so the same bound is encoded in the generated JSON Schema / zod for
# clients; re-exported here (the historical import path) so the column definitions and
# the register guard read it from one place. Raising it there widens columns + guard + the
# client artifacts together.
__all__ = ["KIND_MAX_LENGTH", "BaseModel", "reject_bad_plugin_kind"]


def reject_bad_plugin_kind(kind: str, *, builtin_kinds: frozenset[str], noun: str, owner: str) -> None:
    """The shared boot-time guards a plugin `kind` must pass before it enters a
    `kind`->impl registry: non-empty, not a built-in (a plugin can't silently reshape a
    core default; pick a distinct kind), and within KIND_MAX_LENGTH (fail here, not with
    a write-time DataError on the run-enqueue path). Lives beside KIND_MAX_LENGTH: the
    guard and the column bound are one contract.

    Shared by the three register facades (config class, action impl, source connector),
    which used to triplicate this block and differed only in wording. `noun` names the
    family ("action" / "source") and `owner` the declaring class + attribute for the
    empty-kind message (e.g. "MyConfig.CONFIG_KIND"). The COLLISION check stays with each
    caller: the config registry compares config-class identity, the impl + connector
    registries compare instance type, so it can't be shared here."""
    if not kind:
        raise ValueError(f"{owner} must be non-empty")
    if kind != kind.strip():
        # Mirror the wire contract's reject_builtin_kind (openmagpie_schema._unions): a
        # whitespace-padded kind (" log ") would register cleanly, appear in
        # known_kinds(), and pass the serializer gate, then 400 deep in union validation
        # on EVERY write. Fail loud here at registration instead.
        raise ValueError(f"{owner} {kind!r} must not be padded with whitespace")
    if kind in builtin_kinds:
        raise ValueError(f"{kind!r} is a built-in {noun} kind and cannot be replaced")
    if len(kind) > KIND_MAX_LENGTH:
        raise ValueError(f"{noun} kind {kind!r} exceeds the {KIND_MAX_LENGTH}-char kind column limit")


class BaseModel(models.Model):
    """Abstract base: ULID primary key + created_at/updated_at."""

    id = ULIDField(primary_key=True, editable=False)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        abstract = True
