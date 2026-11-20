"""A feed write refuses a source whose connector needs a server setting this
server lacks (`required_settings`), with how to add it and load it. Covered for
the built-in Product Hunt connector and for a plugin connector declaring one."""

from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from auth_api.operations.signup import SignupOperation
from feeds.models import Feed, Source
from feeds.policy import PolicyError, enforce_connector_settings
from feeds.services.polling import FeedPollOperation
from openmagpie_schema.configs import PluginSourceSpec, ProductHuntLaunchesSourceSpec
from sources import registry as source_registry
from sources.connectors.base import ConnectorParseError
from sources.connectors.producthunt import ProductHuntLaunchesConnector
from sources.required_settings import refusal

PH_SOURCE = {"spec": {"kind": "producthunt_launches"}}


class ProductHuntTokenRequiredTests(TestCase):
    """A feed write refuses a Product Hunt source when this server has no token,
    with how to add it and how to get the server to load it."""

    def setUp(self) -> None:
        user = SignupOperation(email="ph@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=user)

    def _create(self, *, dry_run: bool = False) -> Any:
        body = {
            "name": "launches",
            "kind": "curated",
            "poll_interval_seconds": 1800,
            "data": {"retention_days": 30},
            "sources": [{"spec": {"kind": "producthunt_launches"}}],
        }
        return self.client.post("/v1/feeds" + ("?dry_run=true" if dry_run else ""), body, format="json")

    def _assert_guidance(self, message: str) -> None:
        self.assertIn("PRODUCT_HUNT_TOKEN", message)
        self.assertIn("apps/core/.env", message)
        self.assertIn("make reload-core-env", message)

    @override_settings(PRODUCT_HUNT_TOKEN="")
    def test_create_without_a_token_is_refused_with_guidance(self):
        for dry_run in (False, True):
            response = self._create(dry_run=dry_run)
            self.assertEqual(response.status_code, 400, dry_run)
            self._assert_guidance(response.json()["sources"][0])
        self.assertEqual(Feed.objects.count(), 0)

    def test_setting_sources_without_a_token_is_refused_with_guidance(self):
        response = self.client.post(
            "/v1/feeds",
            {"name": "f", "kind": "curated", "poll_interval_seconds": 300, "data": {"retention_days": 30}},
            format="json",
        )
        with override_settings(PRODUCT_HUNT_TOKEN=""):
            refused = self.client.put(
                f"/v1/feeds/{response.json()['id']}/sources",
                {"sources": [{"spec": {"kind": "producthunt_launches"}}]},
                format="json",
            )
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(list(refused.json()), ["sources"])  # the same shape feed create gives
        self._assert_guidance(refused.json()["sources"][0])

    @override_settings(PRODUCT_HUNT_TOKEN="test-token")
    def test_create_with_a_token_succeeds(self):
        self.assertEqual(self._create().status_code, 201)


class PluginRequiredSettingsTests(TestCase):
    def test_a_plugin_connector_declaring_a_setting_is_refused_without_it(self):
        connector = mock.Mock(kind="custom_source", required_settings={"CUSTOM_KEY": "the custom key"})
        spec = PluginSourceSpec.model_validate({"kind": "custom_source"})
        with mock.patch.dict(source_registry._REGISTRY, {"custom_source": connector}):
            with override_settings(CUSTOM_KEY=""), self.assertRaisesRegex(PolicyError, "CUSTOM_KEY"):
                enforce_connector_settings([spec])
            with override_settings(CUSTOM_KEY="set"):
                enforce_connector_settings([spec])  # no raise

    def test_a_connector_without_required_settings_is_unaffected(self):
        connector = mock.Mock(spec=["kind", "payloads"], kind="custom_source")
        spec = PluginSourceSpec.model_validate({"kind": "custom_source"})
        with mock.patch.dict(source_registry._REGISTRY, {"custom_source": connector}):
            enforce_connector_settings([spec])  # no raise


class SourceWriteSeamTests(TestCase):
    """Source guards run where sources are added, and only there."""

    def setUp(self) -> None:
        user = SignupOperation(email="seam@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=user)

    def _feed(self, sources: list[dict[str, Any]], *, dry_run: bool = False) -> Any:
        body = {"name": "f", "kind": "curated", "poll_interval_seconds": 1800, "data": {"retention_days": 30}}
        return self.client.post(
            "/v1/feeds" + ("?dry_run=true" if dry_run else ""), {**body, "sources": sources}, format="json"
        )

    def test_feed_edit_ignores_sources_so_a_missing_token_does_not_block_it(self):
        with override_settings(PRODUCT_HUNT_TOKEN="test-token"):
            feed_id = self._feed([PH_SOURCE]).json()["id"]
        with override_settings(PRODUCT_HUNT_TOKEN=""):
            edited = self.client.put(
                f"/v1/feeds/{feed_id}",
                {
                    "name": "f",
                    "kind": "curated",
                    "poll_interval_seconds": 3600,
                    "data": {"retention_days": 30},
                    "sources": [PH_SOURCE],
                },
                format="json",
            )
        self.assertEqual(edited.status_code, 200, edited.content)
        self.assertEqual(Feed.objects.get(id=feed_id).poll_interval_seconds, 3600)

    def test_an_unregistered_kind_is_a_400_on_create_and_dry_run(self):
        for dry_run in (False, True):
            response = self._feed([{"spec": {"kind": "custom_source"}}], dry_run=dry_run)
            self.assertEqual(response.status_code, 400, (dry_run, response.content))
            self.assertIn("no connector registered", response.json()["sources"][0])

    @override_settings(SOURCE_BLOCK_PRIVATE_IPS=True)
    def test_a_dry_run_create_runs_the_url_safety_check(self):
        response = self._feed([{"spec": {"kind": "rss", "url": "http://127.0.0.1/feed"}}], dry_run=True)
        self.assertEqual(response.status_code, 400, response.content)

    def test_a_source_already_on_the_feed_is_not_rechecked(self):
        with override_settings(PRODUCT_HUNT_TOKEN="test-token"):
            feed_id = self._feed([PH_SOURCE]).json()["id"]
        rss = {"spec": {"kind": "rss", "url": "https://example.com/feed.xml"}}
        with override_settings(PRODUCT_HUNT_TOKEN=""):
            response = self.client.put(f"/v1/feeds/{feed_id}/sources", {"sources": [PH_SOURCE, rss]}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["added"], 1)


class PollRefusesMissingSettingsTests(TestCase):
    """The poller refuses a source whose connector is missing a required setting,
    for every connector, before the connector is called."""

    def _poll_source(self, spec: Any, connector: Any) -> None:
        feed = Feed.objects.create(account_id="a" * 26, user_id="u" * 26, name="f", kind="curated", data={})
        op = FeedPollOperation(feed)
        source = cast(Source, SimpleNamespace(id="s1", kind=spec.kind, last_event_at=None, field_map=None, meta=None))
        with mock.patch("feeds.services.polling.source_registry.get", return_value=connector):
            op._poll_source(source, spec)

    @override_settings(PRODUCT_HUNT_TOKEN="")
    def test_a_built_in_connector_is_refused_with_guidance(self):
        # Product Hunt's real declaration on a fake, so a missing check can't reach the network.
        connector = mock.Mock(required_settings=ProductHuntLaunchesConnector.required_settings)
        with self.assertRaisesRegex(ConnectorParseError, "PRODUCT_HUNT_TOKEN.*make reload-core-env"):
            self._poll_source(ProductHuntLaunchesSourceSpec(), connector)
        connector.poll.assert_not_called()

    @override_settings(CUSTOM_KEY="")
    def test_a_plugin_connector_is_refused_before_it_polls(self):
        connector = mock.Mock(kind="custom_source", required_settings={"CUSTOM_KEY": "the custom key"})
        with self.assertRaisesRegex(ConnectorParseError, "CUSTOM_KEY"):
            self._poll_source(PluginSourceSpec.model_validate({"kind": "custom_source"}), connector)
        connector.poll.assert_not_called()


@override_settings(PRODUCT_HUNT_TOKEN="")
class RefusalMessageTests(TestCase):
    """The refusal's "how to fix it" comes from the settings profile, not the code."""

    def test_the_local_profile_points_at_the_env_file_and_make_target(self):
        message = refusal("producthunt_launches", ProductHuntLaunchesConnector())
        message = message or ""
        self.assertIn("PRODUCT_HUNT_TOKEN=<value>` to apps/core/.env", message)
        self.assertIn("make reload-core-env", message)

    @override_settings(ENV_RELOAD_HINT="Set {name} in the deployment's secrets and redeploy.")
    def test_another_profile_words_its_own_fix(self):
        message = refusal("producthunt_launches", ProductHuntLaunchesConnector()) or ""
        self.assertTrue(message.endswith(". Set PRODUCT_HUNT_TOKEN in the deployment's secrets and redeploy."), message)
        self.assertNotIn("make", message)


@override_settings(PRODUCT_HUNT_TOKEN="")
class BaseProfileRefusalTests(TestCase):
    @override_settings(ENV_RELOAD_HINT="{name} is not available in the server's environment.")
    def test_the_base_profile_says_the_setting_is_not_available(self):
        message = refusal("producthunt_launches", ProductHuntLaunchesConnector()) or ""
        self.assertTrue(message.endswith(". PRODUCT_HUNT_TOKEN is not available in the server's environment."), message)
