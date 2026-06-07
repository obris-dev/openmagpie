"""Waitlist discriminators (re-exported from the shared schema pkg).

The enum lives in `openmagpie_schema.waitlist_enums` so the server types
against the same source of truth the rest of the stack shares. This module is
the stable in-core import path (`from waitlist.constants import WaitlistState`).

A StrEnum, NOT Django `TextChoices` ; `choices=` stays OFF the DB column (a new
value would otherwise force a migration). The column is a bare CharField storing
the string value ; this enum is the Python-side source of truth for validation
and branching.
"""

from openmagpie_schema.waitlist_enums import WaitlistState

__all__ = ["WaitlistState"]
