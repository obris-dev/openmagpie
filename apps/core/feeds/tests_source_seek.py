"""The watermark seek: `POST /v1/feeds/<id>/sources/seek` (every source on the
feed) + `POST /v1/feed-sources/<id>/seek` (one source by own id). Either
direction, per-direction tally, dry-run preview, the `to` grammar resolved on
the server clock, account isolation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import ulid
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from auth_api.operations.signup import SignupOperation
from feeds.models import Feed, Source
from feeds.policy import PolicyError, enforce_seek_floor

# The scratch feed keeps RETENTION_DAYS of items, deliberately NOT the config
# default (30) so a floor that hard-coded the default would fail the floor tests.
# Fixtures are relative to now so they stay inside that window (a seek below the
# floor is a 400); second-truncated so ISO round trips compare exactly.
RETENTION_DAYS = 10
_NOW = datetime.now(UTC).replace(microsecond=0)
PAST = _NOW - timedelta(days=6)
MID = _NOW - timedelta(days=3)  # between PAST and "now" (the second source's default)


class SourceSeekTests(TestCase):
    def setUp(self) -> None:
        self.user = SignupOperation(email="seek@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/v1/feeds",
            {
                "name": "f",
                "kind": "curated",
                "poll_interval_seconds": 300,
                "data": {"retention_days": RETENTION_DAYS},
                "sources": [
                    # A: explicit past watermark (a backfill window); B: defaults to now.
                    {
                        "spec": {"kind": "rss", "url": "https://a.test/rss", "name": "A"},
                        "last_event_at": PAST.isoformat(),
                    },
                    {"spec": {"kind": "rss", "url": "https://b.test/rss", "name": "B"}},
                ],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.feed_id = resp.json()["id"]
        rows = {
            s["spec"]["name"]: s["id"] for s in self.client.get(f"/v1/feeds/{self.feed_id}/sources").json()["items"]
        }
        self.a_id, self.b_id = rows["A"], rows["B"]
        self.feed_url = f"/v1/feeds/{self.feed_id}/sources/seek"

    def _wm(self, source_id: str) -> datetime:
        value = Source.objects.get(id=source_id).last_event_at
        assert value is not None
        return value

    # ── feed scope ────────────────────────────────────────────────────

    def test_dry_run_tallies_both_directions_without_writing(self) -> None:
        b_before = self._wm(self.b_id)
        resp = self.client.post(self.feed_url, {"to": MID.isoformat(), "dry_run": True}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        # A (PAST) moves forward to MID; B (now) moves backward to MID.
        self.assertEqual(
            {k: body[k] for k in ("advanced", "rewound", "unchanged", "source_count", "dry_run")},
            {"advanced": 1, "rewound": 1, "unchanged": 0, "source_count": 2, "dry_run": True},
        )
        self.assertEqual(datetime.fromisoformat(body["to"]), MID)
        self.assertEqual(self._wm(self.a_id), PAST)  # preview wrote nothing
        self.assertEqual(self._wm(self.b_id), b_before)

    def test_apply_moves_every_row_to_target(self) -> None:
        resp = self.client.post(self.feed_url, {"to": MID.isoformat()}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual((body["advanced"], body["rewound"], body["unchanged"], body["dry_run"]), (1, 1, 0, False))
        self.assertEqual(self._wm(self.a_id), MID)
        self.assertEqual(self._wm(self.b_id), MID)
        # Idempotent: a second seek to the same instant touches nothing.
        again = self.client.post(self.feed_url, {"to": MID.isoformat()}, format="json").json()
        self.assertEqual((again["advanced"], again["rewound"], again["unchanged"]), (0, 0, 2))

    def test_to_is_required(self) -> None:
        # No implicit "now": an empty body (or a blank `to`) must not move a whole
        # feed to the present. Both forms 400 keyed at `to`, nothing written.
        for body in ({}, {"to": ""}):
            with self.subTest(body=body):
                resp = self.client.post(self.feed_url, body, format="json")
                self.assertEqual(resp.status_code, 400, resp.content)
                self.assertIn("to", resp.json())
        self.assertEqual(self._wm(self.a_id), PAST)

    def test_non_object_body_is_400_not_500(self) -> None:
        # DRF parses a bare JSON array; the serializer rejects a non-mapping with a
        # 400 on both routes (no AttributeError from a hand-rolled `.get`).
        for url in (self.feed_url, f"/v1/feed-sources/{self.a_id}/seek"):
            with self.subTest(url=url):
                resp = self.client.post(url, [{"to": "now"}], format="json")
                self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(self._wm(self.a_id), PAST)

    def test_keyword_now_seeks_to_server_now(self) -> None:
        before = timezone.now()
        resp = self.client.post(self.feed_url, {"to": "now"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        to = datetime.fromisoformat(resp.json()["to"])
        self.assertTrue(before <= to <= timezone.now())
        self.assertEqual(self._wm(self.a_id), to)  # the backfill window is gone: A now starts at `to`

    # ── by own id ─────────────────────────────────────────────────────

    def test_seek_by_id_moves_only_that_row(self) -> None:
        b_before = self._wm(self.b_id)
        resp = self.client.post(f"/v1/feed-sources/{self.a_id}/seek", {"to": MID.isoformat()}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual((body["advanced"], body["rewound"], body["unchanged"], body["source_count"]), (1, 0, 0, 1))
        self.assertEqual(self._wm(self.a_id), MID)
        self.assertEqual(self._wm(self.b_id), b_before)

    def test_seek_by_unknown_id_is_404(self) -> None:
        resp = self.client.post(f"/v1/feed-sources/{ulid.ulid()}/seek", {"to": "now"}, format="json")
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_account_isolation(self) -> None:
        other = APIClient()
        other.force_authenticate(
            user=SignupOperation(email="seek-other@example.com", password="Str0ng-Passw0rd!").run()
        )
        self.assertEqual(
            other.post(f"/v1/feed-sources/{self.a_id}/seek", {"to": "now"}, format="json").status_code, 404
        )
        self.assertEqual(other.post(self.feed_url, {"to": "now"}, format="json").status_code, 404)
        self.assertEqual(self._wm(self.a_id), PAST)  # untouched by the foreign attempts

    def test_non_curated_feed_refusal_is_keyed_at_detail_not_to(self) -> None:
        # A feed kind without sources is a refusal about the FEED, not the `to`
        # value; it lands at `detail` like the sibling delete, on both routes.
        Feed.objects.filter(id=self.feed_id).update(kind="not-curated")
        for url in (self.feed_url, f"/v1/feed-sources/{self.a_id}/seek"):
            with self.subTest(url=url):
                resp = self.client.post(url, {"to": "now"}, format="json")
                self.assertEqual(resp.status_code, 400, resp.content)
                self.assertIn("detail", resp.json())
                self.assertNotIn("to", resp.json())
        self.assertEqual(self._wm(self.a_id), PAST)

    # ── the retention floor ───────────────────────────────────────────

    def test_to_older_than_retention_is_400_on_both_routes(self) -> None:
        # Items older than the feed's retention were pruned, so a watermark that
        # far back would re-record them as new and re-run every watch on them;
        # the seek refuses below the floor, keyed at `to`, naming the FEED's
        # window (not the config default).
        too_old = (timezone.now() - timedelta(days=RETENTION_DAYS + 1)).isoformat()
        for url in (self.feed_url, f"/v1/feed-sources/{self.a_id}/seek"):
            with self.subTest(url=url):
                resp = self.client.post(url, {"to": too_old}, format="json")
                self.assertEqual(resp.status_code, 400, resp.content)
                self.assertIn(f"retention window ({RETENTION_DAYS} day(s)", resp.json()["to"][0])
        self.assertEqual(self._wm(self.a_id), PAST)

    def test_oldest_resolves_to_the_feeds_floor_on_both_routes(self) -> None:
        # `oldest` is resolved server-side from THIS feed's retention at the
        # request's clock, so the deepest allowed target is always reachable
        # (an explicit `<retention>d` can land a few microseconds below the floor
        # computed a moment later). Case-insensitive, like the keywords.
        for url, raw in ((self.feed_url, "oldest"), (f"/v1/feed-sources/{self.a_id}/seek", " OLDEST ")):
            with self.subTest(url=url):
                before = timezone.now()
                resp = self.client.post(url, {"to": raw}, format="json")
                self.assertEqual(resp.status_code, 200, resp.content)
                to = datetime.fromisoformat(resp.json()["to"])
                window = timedelta(days=RETENTION_DAYS)
                self.assertTrue(before - window <= to <= timezone.now() - window)
        self.assertEqual(self._wm(self.a_id), to)

    def test_to_inside_retention_is_accepted(self) -> None:
        inside = timezone.now() - timedelta(days=RETENTION_DAYS - 1)
        resp = self.client.post(self.feed_url, {"to": inside.isoformat(), "dry_run": True}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_exactly_retention_lands_on_the_floor_on_preview_and_apply(self) -> None:
        # `<retention>d` resolves and is checked against ONE clock read per request,
        # so it lands exactly on the floor both times (a pinned instant from the
        # preview would sit below the apply's later floor and be rejected).
        raw = f"{RETENTION_DAYS}d"
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                resp = self.client.post(self.feed_url, {"to": raw, "dry_run": dry_run}, format="json")
                self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._wm(self.a_id), datetime.fromisoformat(resp.json()["to"]))

    # ── seek vs an in-flight poll ─────────────────────────────────────

    def test_stale_poll_advance_does_not_undo_a_seek(self) -> None:
        # The poller reads a row at cycle start, fetches, then advances. A seek
        # that lands in between must survive: the advance is a compare-and-set on
        # the watermark the poll was cut from, so the stale write matches no row.
        from feeds.services.sources import SourceService

        svc = SourceService(account_id=Source.objects.get(id=self.b_id).account_id)
        stale = Source.objects.get(id=self.b_id)  # the poller's in-flight copy (watermark = now-ish)
        resp = self.client.post(f"/v1/feed-sources/{self.b_id}/seek", {"to": MID.isoformat()}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        assert stale.last_event_at is not None
        newer = stale.last_event_at + timedelta(minutes=1)
        self.assertEqual(svc.advance_watermark(stale, newer), 0)  # lost the race: no row matched
        self.assertEqual(self._wm(self.b_id), MID)  # the rewind survived
        # The happy path: an advance from a FRESH read still moves the row forward.
        fresh = Source.objects.get(id=self.b_id)
        self.assertEqual(svc.advance_watermark(fresh, newer), 1)
        self.assertEqual(self._wm(self.b_id), newer)

    # ── the `to` grammar (server-resolved) ────────────────────────────

    def test_keyword_yesterday_is_utc_midnight(self) -> None:
        resp = self.client.post(self.feed_url, {"to": "yesterday", "dry_run": True}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        to = datetime.fromisoformat(resp.json()["to"]).astimezone(UTC)
        expected = timezone.now().astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        self.assertEqual(to, expected)

    def test_duration_is_measured_back_from_server_now(self) -> None:
        before = timezone.now()
        resp = self.client.post(self.feed_url, {"to": "2h", "dry_run": True}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        to = datetime.fromisoformat(resp.json()["to"])
        self.assertTrue(before - timedelta(hours=2) <= to <= timezone.now() - timedelta(hours=2))

    def test_garbage_to_is_400_keyed_at_to(self) -> None:
        resp = self.client.post(self.feed_url, {"to": "last tuesday"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("to", resp.json())
        self.assertEqual(self._wm(self.a_id), PAST)

    def test_future_to_is_400(self) -> None:
        future = (timezone.now() + timedelta(days=1)).isoformat()
        resp = self.client.post(self.feed_url, {"to": future}, format="json")
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertTrue(resp.json()["to"][0].startswith("to is in the future"), resp.content)
        self.assertEqual(self._wm(self.a_id), PAST)

    def test_form_encoded_dry_run_false_applies(self) -> None:
        # DRF's FormParser makes every value a string; the BooleanField coerces
        # "false" to False (bool("false") would be True and silently preview).
        resp = self.client.post(self.feed_url, {"to": MID.isoformat(), "dry_run": "false"})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(resp.json()["dry_run"])
        self.assertEqual(self._wm(self.a_id), MID)


class SeekFloorPolicyTests(SimpleTestCase):
    """`enforce_seek_floor` against a fixed clock: the boundary is exactly
    `now - retention_days` (an item recorded then still exists, so it dedups)."""

    NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

    def test_at_and_inside_the_floor_pass(self) -> None:
        floor = self.NOW - timedelta(days=30)
        self.assertEqual(enforce_seek_floor(floor, retention_days=30, now=self.NOW), floor)
        inside = floor + timedelta(seconds=1)
        self.assertEqual(enforce_seek_floor(inside, retention_days=30, now=self.NOW), inside)

    def test_below_the_floor_raises_naming_the_window(self) -> None:
        below = self.NOW - timedelta(days=30, seconds=1)
        with self.assertRaises(PolicyError) as ctx:
            enforce_seek_floor(below, retention_days=30, now=self.NOW)
        self.assertIn("30 day(s)", str(ctx.exception))
        self.assertIn("retention", str(ctx.exception))
