"""The shared extensible-union error filter (openmagpie_schema.errors.clean_union_errors).

Pins that an operator sees each mistake once, per-field: the built-in-branch
discriminator noise (union_tag_invalid / union_tag_not_found) is dropped, the fallback
branch's shared-field duplicate is dropped for a built-in kind, and the internal
`tagged-union[...]` / plugin-member loc segments are stripped. (The source side is
covered in feeds/tests_plugin_source_kinds.py; the CLI rendering in the cli suite.)
"""

from __future__ import annotations

from django.test import SimpleTestCase
from pydantic import ValidationError

from openmagpie_schema.errors import clean_union_errors
from openmagpie_schema.watch import build_watch_action_input, watch_action_wire_adapter


class CleanUnionErrorsTests(SimpleTestCase):
    def _paths(self, payload: dict) -> list[str]:
        try:
            watch_action_wire_adapter.validate_python(payload)
        except ValidationError as e:
            return [".".join(str(p) for p in x["loc"]) or "_" for x in clean_union_errors(e.errors())]
        raise AssertionError("expected ValidationError")

    def test_builtin_shared_field_error_not_duplicated(self) -> None:
        # A built-in kind with a bad shared field: the typed branch reports `log.rank`;
        # the fallback branch's duplicate (bare `rank`) is dropped, not shown twice.
        self.assertEqual(self._paths({"kind": "log", "rank": "x"}), ["log.rank"])

    def test_missing_kind_has_no_discriminator_noise(self) -> None:
        # A missing kind drops the internal union_tag_not_found "_" line, leaving the
        # clean `kind: Field required`.
        self.assertEqual(self._paths({"rank": 0}), ["kind"])

    def test_config_key_matching_member_name_not_swallowed(self) -> None:
        # A built-in row whose typed config has a key literally equal to a plugin member
        # class name must NOT be misclassified as the fallback branch (which would drop
        # the real error, yielding an empty 400 body) nor have that key stripped from the
        # path. A member name counts as the union marker only at the boundary position.
        try:
            build_watch_action_input(
                kind="webhook", config={"url": "https://x.test", "headers": {"PluginActionInput": 123}}
            )
        except ValidationError as e:
            paths = [".".join(str(p) for p in x["loc"]) for x in clean_union_errors(e.errors())]
        else:
            raise AssertionError("expected ValidationError")
        self.assertEqual(paths, ["webhook.config.headers.PluginActionInput"])

    def test_plain_discriminated_union_tag_error_preserved(self) -> None:
        # This filter is wired into the codebase-wide DRF mapper. A PLAIN
        # Field(discriminator=...) union (not one of ours) emits a bare union_tag_invalid
        # with no extensible-union marker in its loc; it MUST survive, or the endpoint
        # returns a 400 with an empty error body. Only OUR unions' tag errors (which
        # carry a `tagged-union[...]` / plugin-member marker) are dropped as noise.
        plain = [{"type": "union_tag_invalid", "loc": (), "msg": "Input tag 'zzz' not a valid discriminator"}]
        self.assertEqual(clean_union_errors(plain), plain)
        marked = [{"type": "union_tag_invalid", "loc": ("tagged-union[A,B]",), "msg": "x"}]
        self.assertEqual(clean_union_errors(marked), [])

    def test_nested_discriminated_union_tag_error_survives_outer_marker(self) -> None:
        # The exact extension pattern this PR enables: a FORK typed member whose config
        # NESTS a plain discriminated union. A bad inner tag produces a genuine tag error
        # DEEP in the loc, but the loc still carries the OUTER extensible union's
        # `tagged-union[...]` prefix. The drop is scoped to the marker being LAST (the
        # error's own governing union); here a config path follows the marker, so the
        # inner error must survive (marker stripped) rather than be swallowed -> empty 400.
        nested = [
            {
                "type": "union_tag_invalid",
                "loc": ("tagged-union[ForkA,ForkB]", "fork", "config"),
                "msg": "Input tag 'bad' not a valid discriminator",
            }
        ]
        cleaned = clean_union_errors(nested)
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["loc"], ("fork", "config"))  # outer marker stripped, error kept
