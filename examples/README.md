# Examples: starter feeds and watches

A starter is a ready-to-run `feed.yaml` + `watch.yaml` pair under
`examples/starters/<name>/`. Each one wires a curated feed (a couple of
subreddits) to a watch that runs a semantic filter, then logs the matches.

Available starters:

- `selfhosted-opensource`: r/selfhosted + r/opensource, listening for people
  asking for an open-source or self-hostable alternative to a paid tool.
- `devtools`: r/devops + r/programming, listening for people hitting a problem
  a developer tool could solve, or asking for a tool recommendation.

## Quickest path: seed it

```
make quickstart        # env + build + migrate, then seeds and ticks
```

`make quickstart` seeds the `selfhosted-opensource` starter into a local dev
account and, if your Ollama is reachable, runs one tick so you see matches right
away. Run a different starter or a wider lookback on demand:

```
make local-seed STARTER=devtools DAYS=7
```

The seed creates the account, user, feed, and watch. It also sets each source's
first-tick watermark to `now - DAYS` so the opening tick scores a backlog
instead of only brand-new posts. The dev login is `local@openmagpie.local`.

## Where matches show up

- In the tick's terminal output: the `log` action writes a `[starter]` line per
  match. Run a tick with `make local-tick` (poll, then trigger, drain, flush).
- In the CLI activity log: `magpie watch action activity <action_id>` (run
  `magpie auth login` first).

The web UI does not show matches yet, so check the terminal or the CLI.

## Applying a starter by hand

The YAML files double as `magpie feed/watch create -f` inputs:

```
magpie feed create -f examples/starters/selfhosted-opensource/feed.yaml
magpie watch create -f examples/starters/selfhosted-opensource/watch.yaml
```

The seed flow does two things for you that the hand path does not. When you
apply by hand, do them yourself:

- Set a past `last_event_at` on each source in `feed.yaml`. Without it the first
  tick only sees brand-new posts, so there is nothing to score yet.
- Put the real feed id into `feed_ids` in `watch.yaml`, replacing the
  `REPLACE_WITH_FEED_ID` placeholder. The feed id prints when you create the
  feed.

## Upgrading to a push

Each starter watch ends with a commented `webhook` action. Uncomment it and
point `url` at your notifier (ntfy, a relay, a Slack or Discord webhook) to get
pushed instead of (or alongside) the log line. A webhook also records a delivery
audit you can inspect with `magpie watch action deliveries`.
