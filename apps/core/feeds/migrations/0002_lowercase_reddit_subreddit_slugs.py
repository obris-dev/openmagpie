"""Normalize legacy reddit_subreddit slugs to the validator's canonical form.

The `RedditSubredditSourceSpec` slug validator (strip `r/`, lowercase) landed
after some sources were created, so existing rows can hold a mixed-case slug and
a `spec_hash` computed from that case-preserved dump. Polling already validates
each spec in memory (so it fetches the lowercased URL fine), but the STALE hash
makes a later `set_sources` re-save treat the sub as a new source and cold-start
its watermark. This rewrites each such row IN PLACE - lowercased slug + recomputed
hash, `last_event_at` preserved - and collapses any case-only duplicate onto a
single deterministic survivor (ordered by id) that keeps the furthest-progressed
`last_event_at` (max), so the merge loses no progress and replays identically.
No-op on a fresh DB (no rows) and idempotent (canonical rows are skipped). Reverse
is a noop - the original casing isn't recoverable, and lowercase is canonical.

Self-contained (house migration pattern, cf. watches/0006, 0008): the slug
normalization and the spec_hash canonicalization are INLINED here, not imported
from live code, so the replay is deterministic and a legacy slug the current
validator would reject can't abort the migrate - we only re-case it, never
validate it. The inlined logic must stay in sync with
RedditSubredditSourceSpec._validate_subreddit + sources._hash_spec (the latter is
pinned by feeds.tests.SpecHashCanonicalTests).
"""

import hashlib
import json
import re

from django.db import migrations

_R_PREFIX = re.compile(r"^/?r/", re.IGNORECASE)


def _canonical_spec(subreddit: str) -> dict:
    # Mirrors RedditSubredditSourceSpec._validate_subreddit: strip a pasted r/
    # prefix + lowercase. No char/length validation, so an out-of-spec legacy
    # slug is re-cased, never rejected (the migrate can't abort on one).
    return {"kind": "reddit_subreddit", "subreddit": _R_PREFIX.sub("", subreddit.strip()).lower()}


def _spec_hash(spec: dict) -> str:
    # Mirrors feeds.services.sources._hash_spec.
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _normalize_reddit_slugs(apps, schema_editor):
    Source = apps.get_model("feeds", "Source")
    # order_by("id"): deterministic survivor on a case-only collapse (the
    # first-seen variant is canonicalized; later ones merge into it), so a replay
    # picks the same survivor instead of relying on unspecified scan order.
    for row in Source.objects.filter(kind="reddit_subreddit").order_by("id").iterator():
        new_spec = _canonical_spec((row.spec or {}).get("subreddit", ""))
        new_hash = _spec_hash(new_spec)
        if row.spec == new_spec and row.spec_hash == new_hash:
            continue  # already canonical
        survivor = (
            Source.objects.filter(account_id=row.account_id, feed_id=row.feed_id, spec_hash=new_hash)
            .exclude(id=row.id)
            .first()
        )
        if survivor is not None:
            # Case-only duplicate collapsing onto the canonical sibling. Keep the
            # FURTHEST-progressed watermark (max) on the survivor before dropping
            # this row, so the merge loses no progress and never rewinds.
            if row.last_event_at is not None and (
                survivor.last_event_at is None or row.last_event_at > survivor.last_event_at
            ):
                survivor.last_event_at = row.last_event_at
                survivor.save(update_fields=["last_event_at"])
            row.delete()
            continue
        row.spec = new_spec
        row.spec_hash = new_hash
        row.save(update_fields=["spec", "spec_hash"])


class Migration(migrations.Migration):
    dependencies = [("feeds", "0001_initial")]
    operations = [migrations.RunPython(_normalize_reddit_slugs, migrations.RunPython.noop)]
