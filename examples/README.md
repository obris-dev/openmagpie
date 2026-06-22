# Examples: starter feeds and watches

A starter is a ready-to-apply `feed.yaml` + `watch.yaml` pair under
`examples/starters/<name>/`. Each one wires a curated feed (a couple of
subreddits) to a watch that runs a semantic filter, then logs the matches.
Apply one by hand, or copy it as a starting point for your own.

Available starters:

- `selfhosted-opensource`: r/selfhosted + r/opensource, listening for people
  asking for an open-source or self-hostable alternative to a paid tool.
- `devtools`: r/devops + r/programming, listening for people hitting a problem
  a developer tool could solve, or asking for a tool recommendation.
- `hackernews`: HN new stories (`hn_feed`), listening for open-source project
  launches. Low-volume (~1k stories/day), safe to apply as-is.
- `hackernews-comments`: a keyword-filtered slice of HN's comment stream
  (`hn_comment`), for monitoring mentions of a project or product. Kept separate
  from `hackernews` on purpose: comments are high-volume, so **read the local-
  processing warning at the top of its `feed.yaml`** and keep the keyword tight
  before applying: a broad keyword can outrun a local engine.

Both HN starters take a `query` keyword that runs **server-side as a pre-filter**:
it narrows which items the connector pulls into the feed at all, before any watch
takes action on them, so it bounds what gets ingested (and the work that follows)
rather than filtering after the fact. It's optional for stories but required for
comments, to keep a watch on the comment stream focused. It supports operators: AND by default, `match: any` for OR, `-word` to exclude, and
`"phrase"`. The exact syntax is in each starter's `feed.yaml`.

## Applying a starter by hand

The YAML files are `magpie feed/watch create -f` inputs. They are templates, not
copy-paste-ready: the two edits below are required first (the `watch create`
rejects the `REPLACE_WITH_FEED_ID` placeholder as written).

```
magpie feed create -f examples/starters/selfhosted-opensource/feed.yaml
magpie watch create -f examples/starters/selfhosted-opensource/watch.yaml
```

Two edits to make first:

- Set a past `last_event_at` on each source in `feed.yaml`. Without it the feed
  only scores posts going forward, so to test your watch you would have to wait
  for new posts to arrive at the source.
- Put the real feed id into `feed_ids` in `watch.yaml`, replacing the
  `REPLACE_WITH_FEED_ID` placeholder. The feed id prints when you create the
  feed.

## Set it up with an AI assistant

Don't want to hand-write YAML? Let an assistant build your config by interviewing
you. This works best with a coding agent that has shell access (Claude Code,
Codex, Gemini CLI): it reads the docs, asks what you want, writes the files, runs
the commands, and fixes any validation error itself. A chat LLM (ChatGPT,
Claude.ai) works too; you just run the commands it gives you. Paste this in:

```
I want to set up OpenMagpie, an open-source social-listening tool, to watch for
something. Read its docs at https://openmagpie.ai/llms-full.txt (or the README,
config/README.md, and examples/ in this repo, if you have it cloned) for the
config schema, the source kinds (Reddit, Hacker News, RSS), and worked examples.

Interview me first: ask what I want to catch, which sources fit, how strict to be,
and where matches should go (the logs or a webhook). Then, from my answers, write
a feed.yaml and a watch.yaml, using a semantic_filter with a clear plain-language
instruction and keeping any source query tight.

If you can run shell commands, create the files and run the `magpie feed create -f`
and `magpie watch create -f` commands yourself, then fix any validation error
`create` reports and retry. Otherwise, give me the files and the commands to run.
```

If your assistant can't browse the web, paste the contents of
[openmagpie.ai/llms-full.txt](https://openmagpie.ai/llms-full.txt) into the chat
first (or run `magpie feed template` and `magpie watch template` and paste those).
`create` validates everything, so the assistant just iterates against any error.

Prefer a guided walk-through instead? `magpie quickstart` does one interactively.

## Where matches show up

- In the pipeline's terminal output: the `log` action writes one line per match,
  tagged with the watch's prefix (e.g. `[oss starter]`, `[devtools starter]`).
  Run a pass with `make local-tick` (poll, then trigger, drain, flush).
- In the CLI activity log: `magpie activity summary --action <action_id>` (run
  `magpie auth login` first).

The web UI does not show matches yet, so check the terminal or the CLI.

## Upgrading to a push

Each starter watch ends with a commented `webhook` action. Uncomment it and
point `url` at your notifier (ntfy, or a relay like a Slack/Discord webhook or
openclaw-style instance) to get pushed instead of (or alongside) the log line. A
webhook also records a delivery audit you can inspect with
`magpie delivery list --action <action_id>`.
