"""Mailer discriminators (re-exported from the shared schema pkg).

`EmailState` lives in `openmagpie_schema.email_enums` so the server types
against one source of truth. This is the stable in-core import path
(`from mailer.constants import EmailState`). StrEnum, NOT Django `TextChoices`;
`choices=` stays OFF the DB column (a new value would otherwise force a
migration). The column is a bare CharField storing the string value.
"""

from openmagpie_schema.email_enums import EmailState

__all__ = ["EmailState"]
