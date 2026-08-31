"""A plugin (non-built-in) source kind, via the extensible SourceSpec union.

The source side was already runtime-registrable (`sources.registry.register`, and
the write gate reads the registry live); the only gap was the closed `SourceSpec`
union. These tests prove a non-built-in kind now validates + renders via the plugin
fallback member, while built-ins keep their typed members and the connector gate
still rejects unregistered kinds.
"""

from __future__ import annotations

from typing import Any, cast, get_args
from unittest import mock

from django.test import SimpleTestCase, override_settings
from pydantic import TypeAdapter, ValidationError

from common.models import KIND_MAX_LENGTH
from feeds.policy import PolicyError, enforce_source_spec_safety
from feeds.services.sources import _assert_connector_registered
from openmagpie_schema.configs import (
    _BUILTIN_SOURCE_KINDS,
    HackerNewsCommentSourceSpec,
    HackerNewsFeedSourceSpec,
    PluginSourceSpec,
    RedditSubredditSourceSpec,
    RssSourceSpec,
    SourceSpec,
    TwitterSearchSourceSpec,
    _BuiltinSourceSpec,
    canonical_spec,
)
from openmagpie_schema.feed import SourceWire
from sources import registry as source_registry

_spec_adapter: TypeAdapter[SourceSpec] = TypeAdapter(SourceSpec)


class SourceSpecFallbackTests(SimpleTestCase):
    def test_plugin_kind_validates_to_fallback_keeping_extras(self) -> None:
        spec = _spec_adapter.validate_python({"kind": "custom_source", "name": "My Source", "foo": "bar"})
        self.assertIsInstance(spec, PluginSourceSpec)
        self.assertEqual(spec.kind, "custom_source")
        # extra="allow" keeps the opaque blob fields for the spec_hash basis.
        self.assertEqual((spec.model_extra or {}).get("foo"), "bar")

    def test_builtin_kind_still_selects_its_typed_member(self) -> None:
        spec = _spec_adapter.validate_python({"kind": "rss", "url": "https://a.test/rss", "name": "N"})
        self.assertIsInstance(spec, RssSourceSpec)

    def test_builtin_kind_with_bad_spec_raises_not_absorbed(self) -> None:
        # A built-in kind with an invalid spec fails its typed member; the fallback
        # rejects built-in kinds, so the whole union raises rather than absorbing it.
        with self.assertRaises(ValidationError):
            _spec_adapter.validate_python({"kind": "rss", "url": "not-a-url"})

    def test_plugin_source_kind_rejects_whitespace(self) -> None:
        # The shared kind validator (reject_builtin_kind) rejects a whitespace-only or
        # padded kind on the source family too, not just the action family.
        for bad in ("   ", " custom_source ", " rss "):
            with self.assertRaises(ValidationError):
                _spec_adapter.validate_python({"kind": bad, "name": "X"})

    def test_plugin_display_prefers_label_else_kind(self) -> None:
        labeled = PluginSourceSpec.model_validate({"kind": "custom_source", "name": "Pretty"})
        self.assertEqual(labeled.display(), "Pretty")
        bare = PluginSourceSpec(kind="custom_source")
        self.assertEqual(bare.display(), "custom_source")

    def test_canonical_spec_is_order_independent_for_plugin(self) -> None:
        a = _spec_adapter.validate_python({"kind": "custom_source", "a": 1, "b": 2})
        b = _spec_adapter.validate_python({"kind": "custom_source", "b": 2, "a": 1})
        self.assertEqual(canonical_spec(a), canonical_spec(b))

    def test_source_wire_renders_plugin_source_via_display(self) -> None:
        wire = SourceWire.model_validate({"id": "s1", "spec": {"kind": "custom_source", "name": "X"}})
        self.assertEqual(wire.model_dump(mode="json")["display"], "X")


class PluginSourceSsrfTests(SimpleTestCase):
    """The write-time SSRF gate. A built-in spec is checked only on its declared
    fetched-URL fields (URL_FIELDS), so a display-only field isn't a false 400; the
    plugin fallback blob is scanned in full (defense-in-depth for a fork), rejecting a
    private / link-local IP host while a hostname (re-checked at poll time) passes."""

    @override_settings(SOURCE_BLOCK_PRIVATE_IPS=True)
    def test_plugin_spec_ip_literal_url_rejected(self) -> None:
        spec = _spec_adapter.validate_python({"kind": "custom_source", "hook": "http://169.254.169.254/latest/meta"})
        with self.assertRaises(PolicyError):
            enforce_source_spec_safety([spec])

    @override_settings(SOURCE_BLOCK_PRIVATE_IPS=True)
    def test_plugin_blob_cannot_smuggle_url_fields_to_skip_scan(self) -> None:
        # extra="allow" makes a blob "URL_FIELDS" key getattr-visible; the isinstance
        # short-circuit must still force the full-blob scan, so this can't steer the gate
        # away from the real URL. (Pins the load-bearing isinstance-first ordering.)
        spec = _spec_adapter.validate_python(
            {"kind": "custom_source", "URL_FIELDS": [], "endpoint": "http://10.0.0.1/x"}
        )
        with self.assertRaises(PolicyError):
            enforce_source_spec_safety([spec])

    @override_settings(SOURCE_BLOCK_PRIVATE_IPS=True)
    def test_plugin_spec_hostname_url_passes(self) -> None:
        spec = _spec_adapter.validate_python({"kind": "custom_source", "hook": "https://example.test/hook"})
        enforce_source_spec_safety([spec])  # hostname: passes here, re-checked at poll time

    @override_settings(SOURCE_BLOCK_PRIVATE_IPS=True)
    def test_plugin_spec_malformed_url_is_skipped_not_500(self) -> None:
        # A malformed URL in the blob (urlparse itself raises ValueError, e.g. an unclosed
        # IPv6 literal) must be skipped, not surface as a 500. The scan feeds arbitrary
        # plugin strings, so this is reachable.
        spec = _spec_adapter.validate_python({"kind": "custom_source", "hook": "http://[::1"})
        enforce_source_spec_safety([spec])  # no raise (skipped)

    @override_settings(SOURCE_BLOCK_PRIVATE_IPS=True)
    def test_builtin_fetched_url_field_rejected(self) -> None:
        spec = _spec_adapter.validate_python({"kind": "rss", "url": "http://10.0.0.1/feed"})
        with self.assertRaises(PolicyError):
            enforce_source_spec_safety([spec])

    @override_settings(SOURCE_BLOCK_PRIVATE_IPS=True)
    def test_builtin_display_only_field_not_scanned(self) -> None:
        # `name` is display-only (not in RssSourceSpec.URL_FIELDS); a private-IP-looking
        # string there is never fetched, so it must NOT 400 (the pre-generalization
        # behavior, which the all-leaves scan had regressed).
        spec = _spec_adapter.validate_python(
            {"kind": "rss", "url": "https://example.test/feed", "name": "http://10.0.0.1/not-fetched"}
        )
        enforce_source_spec_safety([spec])  # no raise

    @override_settings(SOURCE_BLOCK_PRIVATE_IPS=True)
    def test_undeclared_typed_spec_scanned_fail_safe(self) -> None:
        # A typed spec (not the plugin blob) that did NOT declare URL_FIELDS is scanned
        # in FULL (fail-safe), so a fork's typed member is covered even without opting
        # in. Also exercises the mode="json" dump: an HttpUrl field is scanned as its
        # string, not skipped as a Url object.
        from typing import cast

        from pydantic import BaseModel, HttpUrl

        class _ForkSpec(BaseModel):  # deliberately declares no URL_FIELDS
            kind: str = "fork_typed"
            endpoint: HttpUrl

        spec = _ForkSpec(endpoint=cast(HttpUrl, "http://169.254.169.254/latest"))  # link-local metadata IP
        with self.assertRaises(PolicyError):
            enforce_source_spec_safety([cast(SourceSpec, spec)])


class SourceUnionErrorRenderingTests(SimpleTestCase):
    """A malformed BUILT-IN source spec surfaces clean per-field errors, not the
    extensible-union's `tagged-union[...]` prefix + the plugin fallback's built-in-kind
    contract line (mirrors the action family, via the shared clean_union_errors)."""

    def test_server_drf_body_is_clean(self) -> None:
        from common.pydantic_errors import pydantic_errors_to_drf

        try:
            _spec_adapter.validate_python({"kind": "rss", "url": "not-a-url"})
            self.fail("expected ValidationError")
        except ValidationError as e:
            drf = pydantic_errors_to_drf(e)
        blob = str(drf)
        self.assertNotIn("tagged-union", blob)  # union machinery stripped
        self.assertNotIn("built-in kind", blob)  # plugin fallback's contract line dropped
        self.assertTrue(any("url" in key for key in drf))  # the real per-field path survives

    def test_server_drf_body_clean_for_padded_fallback_kind(self) -> None:
        # A whitespace-padded kind fails the fallback branch; the built-in branch's
        # discriminator-mismatch (union_tag_invalid) + the member-name prefix must not
        # leak alongside the real "kind must not be padded" error.
        from common.pydantic_errors import pydantic_errors_to_drf

        try:
            _spec_adapter.validate_python({"kind": " rss ", "url": "https://x.test/f"})
            self.fail("expected ValidationError")
        except ValidationError as e:
            drf = pydantic_errors_to_drf(e)
        blob = str(drf)
        self.assertNotIn("tagged-union", blob)
        self.assertNotIn("does not match any of the expected tags", blob)  # union_tag_invalid dropped
        # EXACT key (not a substring): if a fallback member-class rename ever silently
        # no-ops the loc stripping, the key becomes `<NewName>.kind` and this fails,
        # mirroring the action side's exact-path test.
        self.assertEqual(list(drf), ["kind"])

    def test_multi_row_does_not_swallow_sibling_errors(self) -> None:
        # A malformed BUILT-IN row must not suppress a sibling row whose only error is on
        # its fallback branch: clean_union_errors scopes the built-in decision per union
        # instance (loc prefix), so both rows' errors reach the operator.
        from common.pydantic_errors import pydantic_errors_to_drf
        from openmagpie_schema.feed import SourceInput

        list_adapter: TypeAdapter[list[SourceInput]] = TypeAdapter(list[SourceInput])
        payload = [
            {"spec": {"kind": "rss", "url": "not-a-url"}},  # built-in, bad url
            {"spec": {"kind": " rss ", "url": "https://x.test/f"}},  # padded -> fallback branch
        ]
        try:
            list_adapter.validate_python(payload)
            self.fail("expected ValidationError")
        except ValidationError as e:
            keys = list(pydantic_errors_to_drf(e))
        self.assertTrue(any(k.startswith("[0]") and "url" in k for k in keys))  # row 0 present
        self.assertTrue(any(k.startswith("[1]") and "kind" in k for k in keys))  # row 1 NOT swallowed


class ConnectorGateTests(SimpleTestCase):
    """The write gate accepts a plugin source kind IFF its connector is registered
    (routing); unchanged behavior, now reachable for non-built-in kinds."""

    def _spec(self) -> SourceSpec:
        return _spec_adapter.validate_python({"kind": "custom_source", "name": "X"})

    def test_registered_plugin_connector_passes_gate(self) -> None:
        connector = mock.Mock()
        connector.kind = "custom_source"
        with mock.patch.dict(source_registry._REGISTRY, {"custom_source": connector}, clear=False):
            _assert_connector_registered([self._spec()])  # no raise

    def test_unregistered_plugin_kind_is_rejected(self) -> None:
        with self.assertRaises(PolicyError):
            _assert_connector_registered([self._spec()])


class RegisterSourceFacadeTests(SimpleTestCase):
    """register_source() registers the connector AND its payloads in one call, so a
    plugin author doesn't juggle the two internal registries."""

    def test_registers_connector_and_payloads(self) -> None:
        from collections.abc import Iterator

        from sources import payload_registry
        from sources.payloads import SourcePayload
        from sources.registry import get, register_source

        class _DummyConnector:
            kind = "custom_source"
            payloads: list[type[SourcePayload]] = []

            def poll(self, spec, since, field_map=None, heartbeat=None) -> Iterator[SourcePayload]:
                return iter(())

            def count(self, spec, since) -> int:
                return 0

        conn = _DummyConnector()
        with (
            mock.patch.dict(source_registry._REGISTRY, clear=False),
            mock.patch.object(payload_registry, "register") as payloads_register,
        ):
            register_source(conn)
            self.assertIs(get("custom_source"), conn)
            payloads_register.assert_called_once_with("custom_source", [])

    def test_register_rejects_builtin_kind(self) -> None:
        # A plugin can't silently replace a core connector: registering a built-in
        # kind raises rather than overwriting it.
        connector = mock.Mock()
        connector.kind = "rss"
        with self.assertRaises(ValueError):
            source_registry.register(connector)

    def test_register_rejects_over_long_kind(self) -> None:
        # A kind longer than the Source.kind column fails at registration, not with a
        # write-time DataError.
        connector = mock.Mock()
        connector.kind = "x" * (KIND_MAX_LENGTH + 1)
        with self.assertRaises(ValueError):
            source_registry.register(connector)

    def test_register_rejects_padded_kind(self) -> None:
        # A whitespace-padded kind boots fine but 400s deep in union validation on every
        # write (the wire contract rejects padding); the boot-time guard mirrors that, so
        # it fails loud at registration instead.
        connector = mock.Mock()
        connector.kind = " rss_ish "
        with self.assertRaises(ValueError):
            source_registry.register(connector)

    def test_register_rejects_duplicate_kind(self) -> None:
        # Two plugins claiming the same source kind is a collision (fail loud), not
        # silent last-wins. Distinct classes so it's not read as idempotent.
        class _C1:
            kind = "dup_src"

        class _C2:
            kind = "dup_src"

        with mock.patch.dict(source_registry._REGISTRY, clear=False):
            source_registry.register(cast(Any, _C1()))
            with self.assertRaises(ValueError):
                source_registry.register(cast(Any, _C2()))


class BuiltinSourceKindInvariantTests(SimpleTestCase):
    """The set the plugin fallback rejects is DERIVED from the built-in union, so it
    can't drift. Pin that: it equals the SOURCE_KIND of every union member (add a 5th
    built-in spec but forget to wire it and this fails loud, mirroring the action
    side, which derives its set from the WatchActionKind enum)."""

    def test_kind_literal_default_matches_source_kind(self) -> None:
        # Cross-pin: the union dispatches on each member's `kind` Literal, the fallback
        # rejects by SOURCE_KIND; they're declared independently per spec. Pin that they
        # agree per member; a divergence would let a malformed built-in spec slip into
        # the fallback (its kind absent from the SOURCE_KIND-derived reject-set).
        members = get_args(get_args(_BuiltinSourceSpec)[0])
        for m in members:
            self.assertEqual(m.model_fields["kind"].default, m.SOURCE_KIND, m.__name__)

    def test_builtin_source_kinds_are_exactly_the_known_builtins(self) -> None:
        self.assertEqual(
            _BUILTIN_SOURCE_KINDS,
            frozenset(
                {
                    RedditSubredditSourceSpec.SOURCE_KIND,
                    RssSourceSpec.SOURCE_KIND,
                    HackerNewsFeedSourceSpec.SOURCE_KIND,
                    HackerNewsCommentSourceSpec.SOURCE_KIND,
                    TwitterSearchSourceSpec.SOURCE_KIND,
                }
            ),
        )

    def test_schema_reject_set_matches_core_connector_registry(self) -> None:
        # The schema's fallback reject-set (openmagpie_schema) and the core connector
        # registry's built-in set (apps/core) name the same kinds; they coincide by
        # convention only, so pin it (mirrors the action side's invariant).
        self.assertEqual(_BUILTIN_SOURCE_KINDS, source_registry._BUILTIN_KINDS)
