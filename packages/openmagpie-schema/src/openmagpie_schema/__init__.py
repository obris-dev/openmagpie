"""Pure Pydantic models shared by core (server) and the magpie CLI.

Zero Django / DRF imports - this is the single source of truth for the
cross-boundary shapes (config + wire), resolved as a uv workspace
member by both core and the magpie CLI (see project memory
project_schema_authority_northstar).
"""

from ._unions import KIND_MAX_LENGTH

__all__ = ["KIND_MAX_LENGTH"]
