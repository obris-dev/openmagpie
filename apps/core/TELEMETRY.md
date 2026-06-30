# Telemetry

OpenMagpie sends **anonymous, opt-out** usage telemetry by default. This document
is the full, honest description of it: what it's for, exactly what is (and isn't)
sent, and every way to turn it off.

## Why it exists

OpenMagpie is open source and self-hosted, which means we otherwise have **no
idea** whether anyone installs it, gets to a first match, or which sources and
features matter. Without that, every decision (build a UI? which connector next?
is setup too hard?) is a guess. A small anonymous signal lets us prioritize what
actually helps the people running it. That's the entire purpose.

## The guarantees

- **On by default, easy to turn off.** A fresh install emits anonymous telemetry
  (the `unset` default), so there's a usage signal without an opt-in step. Turn it
  off any time with `telemetry disable`, `DO_NOT_TRACK=1`, or an empty
  `POSTHOG_API_KEY` (see [How to control it](#how-to-control-it)); once `off`, it
  sends nothing.

> **Upgrading from a pre-opt-out version?** Telemetry used to be opt-IN: an install
> left in the `unset` mode sent **nothing**. After upgrading, that same `unset`
> install **begins emitting anonymous telemetry with no action on your part** (only
> an explicit `off` is silent now). If you don't want that, opt out once with
> `make local-manage CMD="telemetry disable"`, `DO_NOT_TRACK=1`, or an empty
> `POSTHOG_API_KEY`. The CLI also shows a one-time notice on your next
> `magpie auth login`.
- **Anonymous, not pseudonymous.** Events are keyed by a random per-install
  `instance_id` (a UUID minted at first run). It is not linked to your
  account, your email, or anything you monitor. Geolocation is disabled
  (`disable_geoip`), so no location is derived from it; and because events are
  captured server-side, the only IP that reaches PostHog is your instance's own
  outbound server IP (the host running OpenMagpie), never an end user's.
- **Never your content.** We never send queries, filter instructions, URLs,
  post titles, match text, or any feed/watch payload. Only counts, enums, and
  the version (see the schema below).
- **Off means silent.** When off, nothing leaves your machine, including **no
  record that you declined**. There is no opt-out beacon.
- **It can never break the app.** Every emit is best-effort and swallowed; a
  telemetry failure cannot affect a poll, a judgment, or an API call.

## What is collected

Every event also carries `version` (the product version) and
`deployment` (`self_hosted`). Milestone events also carry `surface`: `cli`, `web`,
or `api` for a user-driven action, or `system` for server-internal emits (the
scheduler, e.g. `first_match`).

**Milestone events (rare, one-off):**

| Event | Properties |
|---|---|
| `telemetry_enabled` | (telemetry turned back on after a disable) |
| `feed_created` | `source_count`, `connector_kinds`, `surface` |
| `watch_created` | `action_kinds`, `feed_count`, `surface` |
| `first_match` | `action_kind`, `surface` (a watch's first-ever match) |
| `quickstart_completed` | `surface` |

> Funnel caveat: these fire on every install except those that turned telemetry
> off, so the counts approximate all installs minus opt-outs. The quickstart seeds
> its feed/watch directly (not through the API), so those aren't counted as
> `feed_created`/`watch_created`.

**Daily heartbeat (`instance_heartbeat`, one event per install per day):** current
gauges + a 24h rollup, so a busy install sends one event/day, not thousands.

- environment: `os`, `arch`, `engine_reachable`
- gauges: `accounts`, `feeds`, `watches`, `sources_by_kind`, `actions_by_kind`
- 24h rollup: `runs_by_state`, `matches`, `deliveries`

A sample heartbeat payload:

```json
{
  "event": "instance_heartbeat",
  "distinct_id": "f1e2d3c4-....-randomUUID",
  "properties": {
    "version": "0.3.0", "deployment": "self_hosted",
    "os": "Linux", "arch": "x86_64", "engine_reachable": true,
    "accounts": 1, "feeds": 2, "watches": 1,
    "sources_by_kind": {"reddit_subreddit": 2, "hn_comment": 1},
    "actions_by_kind": {"semantic_filter": 1, "log": 1},
    "runs_by_state": {"succeeded": 4, "gated": 37}, "matches": 4, "deliveries": 0
  }
}
```

## What is NOT collected

Content of any kind: source queries, `semantic_filter` instructions, URLs, post
titles or bodies, match text, account/email, or the LLM model name. (Your
instance's outbound server IP necessarily reaches PostHog like any HTTPS request,
but geolocation is disabled, so no location is derived from it.)

## How to control it

The `magpie` CLI is the universal way (it works against any deployment you can
reach, self-hosted or larger; changing the mode requires an account owner):

- **Status:** `magpie telemetry status`
- **Turn off (opt out):** `magpie telemetry disable`
- **Turn back on:** `magpie telemetry enable`

Environment-level, needs no login or CLI (works headless, and before any setup):

- **Hard off (any mode):** set `DO_NOT_TRACK=1` ([standard](https://consoledonottrack.com/)), or leave `POSTHOG_API_KEY` empty.
- **Send to your own PostHog instead:** set `POSTHOG_API_KEY` (and `POSTHOG_HOST`) to your project.

On the server host you can also run the management command directly (same effect,
no CLI auth): `make local-manage CMD="telemetry disable"` in a dev checkout, or
`manage.py telemetry disable`.

## Where it goes & retention

Anonymous events go to a PostHog project (US region). The key shipped in the
repo is a public, write-only ingestion key (capture-only; it cannot read data).
Events are retained on a rolling window for trend analysis, not archived
indefinitely.

## Identified telemetry

`identified` mode (account-keyed analytics) is reserved for the future *hosted*
product and its terms of service. Self-hosted installs only ever use `off` or
`anonymous`; the self-hosted setter refuses `identified`.
