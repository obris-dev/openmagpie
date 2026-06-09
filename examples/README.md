# Examples: starter feeds and watches

A starter is a ready-to-run `feed.yaml` + `watch.yaml` pair under
`examples/starters/<name>/`. Each one wires a curated feed (a couple of
subreddits) to a watch that runs a semantic filter, then logs the matches.

Available starters:

- `selfhosted-opensource`: r/selfhosted + r/opensource, listening for people
  asking for an open-source or self-hostable alternative to a paid tool.
- `devtools`: r/devops + r/programming, listening for people hitting a problem
  a developer tool could solve, or asking for a tool recommendation.

## Quickstart

```
./scripts/quickstart/run.sh    # check Docker, .env, build, migrate, seed, hooks
```

`run.sh` seeds the `selfhosted-opensource` starter into a local dev account and,
if your Ollama is reachable, runs one tick so you see matches right away. Run a
different starter or a wider lookback on demand:

```
STARTER=devtools DAYS=7 ./scripts/quickstart/seed.sh
```

The seed creates an initial account, user, feed, and watch. It also sets a
watermark on each source to backfill recent posts (the `now - DAYS` window)
instead of waiting for brand-new ones to arrive. The dev login is
`local@openmagpie.local`.

## Where matches show up

- In the tick's terminal output: the `log` action writes one line per match,
  tagged with the starter's prefix (e.g. `[oss starter]`, `[devtools starter]`).
  Run a tick with `make local-tick` (poll, then trigger, drain, flush).
- In the CLI activity log: `magpie watch action activity <action_id>` (run
  `magpie auth login` first).

The web UI does not show matches yet, so check the terminal or the CLI.

## Applying a starter by hand

The YAML files double as `magpie feed/watch create -f` inputs. These are
templates, not copy-paste-ready: the two edits below are required first (the
`watch create` will reject the `REPLACE_WITH_FEED_ID` placeholder as written).

```
magpie feed create -f examples/starters/selfhosted-opensource/feed.yaml
magpie watch create -f examples/starters/selfhosted-opensource/watch.yaml
```

The seed flow does two things for you that the hand path does not. When you
apply by hand, do them yourself:

- Set a past `last_event_at` on each source in `feed.yaml`. Without it the feed
  only scores posts going forward, so to test your watch you would have to wait
  for new posts to arrive at the source.
- Put the real feed id into `feed_ids` in `watch.yaml`, replacing the
  `REPLACE_WITH_FEED_ID` placeholder. The feed id prints when you create the
  feed.

## Upgrading to a push

Each starter watch ends with a commented `webhook` action. Uncomment it and
point `url` at your notifier (ntfy, or a relay like a Slack/Discord webhook or
openclaw-style instance) to get pushed instead of (or alongside) the log line. A
webhook also records a delivery
audit you can inspect with `magpie watch action deliveries`.
