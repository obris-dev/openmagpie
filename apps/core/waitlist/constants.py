"""Waitlist discriminators (re-exported from the shared schema pkg).

The enums live in `openmagpie_schema.waitlist_enums` so the server types
against the same source of truth the rest of the stack shares. This module is
the stable in-core import path (`from waitlist.constants import WaitlistState`).

StrEnums, NOT Django `TextChoices` ; `choices=` stays OFF the DB columns (a new
value would otherwise force a migration). The columns are bare CharFields
storing the string value ; these enums are the Python-side source of truth for
validation and branching.
"""

from openmagpie_schema.waitlist_enums import (
    WaitlistCategory,
    WaitlistSourceInterest,
    WaitlistState,
)

__all__ = ["WaitlistCategory", "WaitlistSourceInterest", "WaitlistState"]
