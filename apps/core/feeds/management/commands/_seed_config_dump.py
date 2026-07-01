"""Seed config helpers: parse the prompt's subreddit input, read the template's
defaults, apply the user's personalization, and write the seeded feed + watch
to `config/quickstart/` as editable YAML (the same shape `magpie feed/watch
create -f` accept). The README + template are committed under `config/`, so the
dump only writes the two personalized YAMLs, not the doc.

Kept out of seed_quickstart.py so that command stays under the file-length cap.
The leading underscore keeps Django's management-command discovery from treating
this module as a command.
"""

import re
from pathlib import Path
from typing import Any

import yaml
from django.core.management.base import CommandError
from pydantic import ValidationError

from openmagpie_schema.configs import RedditSubredditSourceSpec
from openmagpie_schema.watch import WatchActionInput, build_watch_action_input
from openmagpie_schema.watch_enums import WatchActionKind
from watches.policy import PolicyError
from watches.registry import validate_config

# Reddit subreddit names are letters / digits / underscore. We screen on the
# charset only (not length), enough to drop a pasted URL or a spaced phrase
# before it reaches a poll and fails opaquely.
_SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]+$")


def clean_subreddits(raw: str) -> list[str]:
    """Parse a comma-separated subreddit list from the prompt: trim spaces, drop
    a leading `r/` (users type it both ways), de-dup keeping first occurrence,
    and DROP anything that is not a valid subreddit name (a pasted URL, a spaced
    phrase) so it can't reach a poll and fail opaquely. Dropping, not erroring:
    this runs in the interactive seed under `set -e`, where a hard error would
    abort the whole quickstart over one typo; an all-invalid list just falls
    back to the template."""
    out: list[str] = []
    for part in raw.split(","):
        name = part.strip()
        if name.lower().startswith("r/"):
            name = name[2:].strip()
        if _SUBREDDIT_RE.match(name) and name not in out:
            out.append(name)
    return out


def subreddit_sources(subreddits: list[str]) -> list[dict[str, Any]]:
    """Feed `sources` entries for these subreddits, matching the template
    feed.yaml shape (`{spec: {kind, subreddit}}`)."""
    return [{"spec": {"kind": RedditSubredditSourceSpec.SOURCE_KIND, "subreddit": s}} for s in subreddits]


def template_subreddits(feed_yaml: dict[str, Any]) -> list[str]:
    """The subreddit names in the template feed dict, for the prompt default."""
    out: list[str] = []
    for source in feed_yaml.get("sources", []):
        spec = source.get("spec", {}) if isinstance(source, dict) else {}
        if spec.get("kind") == RedditSubredditSourceSpec.SOURCE_KIND and spec.get("subreddit"):
            out.append(str(spec["subreddit"]))
    return out


def _semantic_filter(watch_yaml: dict[str, Any]) -> dict[str, Any] | None:
    """The first semantic_filter action dict, or None."""
    for action in watch_yaml.get("actions", []):
        if isinstance(action, dict) and action.get("kind") == WatchActionKind.SEMANTIC_FILTER:
            return action
    return None


def filter_instructions(watch_yaml: dict[str, Any]) -> str | None:
    """The semantic_filter's `instructions`, for the prompt default. None when
    the watch has no semantic_filter (the caller treats None as 'no default')."""
    action = _semantic_filter(watch_yaml)
    return action.get("config", {}).get("instructions") if action else None


def set_filter_instructions(watch_yaml: dict[str, Any], instructions: str) -> None:
    """Override the semantic_filter's `instructions` in place."""
    action = _semantic_filter(watch_yaml)
    if action is not None:
        action.setdefault("config", {})["instructions"] = instructions


def filter_threshold(watch_yaml: dict[str, Any]) -> float | None:
    """The semantic_filter's `threshold`, for the prompt default. None when
    absent / non-numeric."""
    action = _semantic_filter(watch_yaml)
    value = action.get("config", {}).get("threshold") if action else None
    # `not bool`: bool is an int subclass, so `threshold: true` would otherwise
    # coerce to 1.0 instead of being treated as absent.
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def set_filter_threshold(watch_yaml: dict[str, Any], threshold: float) -> None:
    """Override the semantic_filter's `threshold` in place."""
    action = _semantic_filter(watch_yaml)
    if action is not None:
        action.setdefault("config", {})["threshold"] = threshold


def parse_threshold(raw: str) -> float | None:
    """Parse the prompt's threshold input to a float clamped to [0, 1], or None
    to keep the template's. Empty / non-numeric returns None (never raises):
    this runs in the interactive seed under `set -e`, so a typo must not abort
    the quickstart, and an out-of-range value clamps rather than erroring."""
    try:
        value = float(raw.strip())
    except (ValueError, AttributeError):
        return None
    return max(0.0, min(1.0, value))


def parse_actions(raw_actions: list[dict[str, Any]]) -> list[WatchActionInput]:
    """Turn the raw action dicts into validated, IN-MEMORY WatchActionInput
    objects. No DB writes here; WatchService.create persists them. Mirrors the
    watch serializer: validate_config gives the shape + policy checked typed
    config, and the stored blob is its dump. A bad action surfaces as a clean
    CommandError."""
    actions = []
    for i, raw in enumerate(raw_actions):
        try:
            kind = raw["kind"]
            config = raw["config"]
        except KeyError as exc:
            raise CommandError(f"action {i} is missing key {exc}") from exc
        try:
            typed = validate_config(kind, config)
        except KeyError as exc:
            raise CommandError(f"action {i} has unknown kind {exc}") from exc
        except (ValidationError, PolicyError) as exc:
            raise CommandError(f"action {i} ({kind!r}) is invalid: {exc}") from exc
        actions.append(build_watch_action_input(id="", kind=kind, config=typed.model_dump(mode="json")))
    return actions


def dump_config(config_root: Path, feed_yaml: dict[str, Any], watch_yaml: dict[str, Any], feed_id: str) -> Path:
    """Write the personalized feed.yaml + watch.yaml under `config_root/quickstart/`,
    returning that dir. (The README + template under `config_root` are committed,
    so the dump doesn't touch them.)

    This is the config that was APPLIED to create the feed + watch (the same dicts
    the seed built from), not a read-back of the persisted rows, so re-applying it
    reproduces them. The dumped watch points `feed_ids` at the real feed just
    created; the feed dump carries no `last_event_at` (that backfill watermark is a
    seed-time convenience, not part of a reusable feed config), and emits `data`
    unconditionally because `magpie feed create -f` requires it.
    """
    quickstart_dir = config_root / "quickstart"
    quickstart_dir.mkdir(parents=True, exist_ok=True)
    feed_out = {k: v for k, v in feed_yaml.items() if k != "sources"}
    feed_out.setdefault("data", {})  # required by FeedCreateSerializer; the template may omit it
    feed_out["sources"] = [{"spec": s["spec"]} for s in feed_yaml.get("sources", []) if "spec" in s]
    watch_out = {**watch_yaml, "feed_ids": [feed_id]}
    (quickstart_dir / "feed.yaml").write_text(yaml.safe_dump(feed_out, sort_keys=False, allow_unicode=True))
    (quickstart_dir / "watch.yaml").write_text(yaml.safe_dump(watch_out, sort_keys=False, allow_unicode=True))
    return quickstart_dir
