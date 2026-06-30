"""Test runner that guarantees the suite never emits real telemetry.

Telemetry is opt-OUT, so a throwaway test DB's singleton is `current()`-created in
UNSET mode with a freshly minted instance_id and *emits*. Many tests (feeds /
watches / engine) create feeds / watches / matches that fire telemetry events and
do NOT mock the PostHog client, and the baked-in POSTHOG_API_KEY default means the
key is present in every environment. Without this guard, `manage.py test` (locally
and in CI) would ship anonymous events (with a new per-run instance_id) to the
shared production PostHog project.

Stubbing `posthog.Posthog` for the whole run means `get_client()` builds a harmless
mock instead of a real SDK client, so `capture()` reaches a no-op rather than the
network. Telemetry's own tests still `mock.patch` `get_client` / `posthog.Posthog`
locally to assert emit behavior; those local patches stack over this baseline.
"""

import os
from unittest import mock

from django.test import override_settings
from django.test.runner import DiscoverRunner


class NoTelemetryTestRunner(DiscoverRunner):
    def run_tests(self, *args, **kwargs):
        # Neutralize all THREE ambient emit gates for the whole suite so (a) no real
        # client is ever built and (b) a dev/CI box that sets any of them doesn't flip
        # emission-asserting tests to "no" and fail spuriously:
        #   - posthog.Posthog stubbed -> get_client() returns a harmless mock,
        #   - DO_NOT_TRACK cleared (capture()/enabled() read the env var),
        #   - POSTHOG_API_KEY forced non-empty (frozen at settings import, so it needs
        #     override_settings, not an env patch).
        # Tests that need a gate ON (e.g. test_do_not_track_suppresses) set it locally,
        # stacking over this baseline.
        with (
            mock.patch("posthog.Posthog"),
            mock.patch.dict(os.environ, {"DO_NOT_TRACK": ""}),
            override_settings(POSTHOG_API_KEY="phc_test"),
        ):
            return super().run_tests(*args, **kwargs)
