<p align="center">
  <img src="assets/magpie-logo-on-transparent.png" alt="OpenMagpie" width="220">
</p>

<p align="center">
  Open-source, self-hostable listening for the conversations that matter to you, so you can join in while they're active.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License"></a>
</p>

<p align="center">
  <b><a href="https://www.openmagpie.ai/">openmagpie.ai</a></b> |
  <a href="#quickstart">Quickstart</a> |
  <a href="apps/cli/README.md">CLI reference</a> |
  <a href="CHANGELOG.md">Changelog</a>
</p>

---

<p align="center">
  <img src="assets/cli-tour.gif" alt="magpie CLI tour: the feed's sources, the watch's semantic filter, then the matches with scores and links" width="900">
</p>

> [!TIP]
> **CLI not for you?** A UI / hosted version is on the way. Star the repo for updates, or join the waitlist at [openmagpie.ai](https://www.openmagpie.ai/).

## What it does

You scan Reddit, Hacker News, and a few RSS feeds looking for someone hitting a problem your product solves or asking a question you can answer well. Getting there while the conversation is happening is how you build a brand and a community around what you know. OpenMagpie watches the threads for you so you spend your time on engagement instead of searching.

You curate sources into a feed, write a natural-language description of what's relevant (for example, "someone frustrated with manual social monitoring and asking for alternatives"), and a local LLM run via any OpenAI-compatible runner (e.g. Ollama, vLLM, LM Studio) scores each new post against it. Matches go to a webhook or your logs (more integrations coming); everything else is dropped. You read the hits instead of the firehose.

## Where it listens

OpenMagpie listens wherever communities are having those conversations.

- **Public discussion (today):** Reddit, Hacker News, and any RSS or Atom feed (news, blogs, Substack publications, and forums that publish feeds).
- **Communities you're in (roadmap):** Slack workspaces and LinkedIn you already belong to, so you catch relevant threads in the groups where you participate, no admin or app install required.

## Quickstart

One command for your first real match (needs Docker and uv; it clones the repo and runs the quickstart for you):

```bash
curl -fsSL https://openmagpie.ai | sh
```

Once setup finishes, keep processing new posts in the background:

```bash
make up-jobs        # run the schedulers in the background
tail -f .jobs/*.log # watch them work
```

<img src="assets/quickstart.gif" alt="The quickstart from curl to first matches: prerequisites check, LLM setup prompts, personalizing the listener (which subreddits, what to flag, how strict), then the seeded backlog scoring and the ready summary" width="900">

Prefer to not generate seed data? `SKIP_DATA_SEED=1` brings up the stack without sample data:

```bash
curl -fsSL https://openmagpie.ai | SKIP_DATA_SEED=1 sh
```

Prefer to clone first?

```bash
git clone https://github.com/obris-dev/openmagpie.git
cd openmagpie
./scripts/quickstart/run.sh
```

Either way it walks you through your first listener (which subreddits to watch, what to flag in plain language, how strict), seeds it, and once an LLM is reachable runs the pipeline once so the first matches print straight to the logs, tagged `[quickstart]`. Matches show up in the terminal and the CLI activity log, not the web UI yet. Your feed and watch are saved as editable YAML in `config/quickstart/` (see [config/README.md](config/README.md) for editing and reusing them). Want more posts to start with? `DAYS=7 ./scripts/quickstart/seed.sh` backfills a week instead of a day. See [examples/README.md](examples/README.md) for ready-made starters to apply by hand. Or have an AI assistant interview you and build the config: copy the prompt in [Set it up with an AI assistant](examples/README.md#set-it-up-with-an-ai-assistant).

### Prereq: an OpenAI-compatible LLM endpoint

OpenMagpie is BYO LLM; the dev stack doesn't bundle one. Whatever you already run almost certainly works, because **Ollama, vLLM, llama.cpp, and LM Studio all expose an OpenAI-compatible `/v1` API** (so do hosted providers like OpenAI, Together, or Groq). OpenMagpie talks to that `/v1` endpoint with the standard OpenAI client, so you just point `ENGINE_BASE_URL` at it. The quickstart validates your endpoint and points you at the model of your choice.

- **Local.** Any OpenAI-compatible server on your machine works; point `ENGINE_BASE_URL` at its `/v1`: Ollama (`http://host.docker.internal:11434/v1`), vLLM (`:8000/v1`), LM Studio (`:1234/v1`), or llama.cpp (`:8080/v1`). The shipped default is Ollama's `:11434`. New here and want the quickest start? Install [Ollama](https://ollama.com/download), then `ollama pull qwen2.5:7b && ollama serve`.
- **Remote (LAN box, GPU server, cloud).** Set `ENGINE_BASE_URL=http://your-host:11434/v1` in `apps/core/.env`.
- **Hosted API.** Set `ENGINE_BASE_URL=https://api.openai.com/v1` and `ENGINE_API_KEY=...` (local servers leave the key blank).

Set `ENGINE_MODEL` to the model you want to judge with. A 7B model judges in roughly 1 to 3 seconds on Apple Silicon or a recent NVIDIA GPU; CPU-only works but is slower.

### Use it

The quickstart already built and seeded everything and put the `magpie` CLI on your `PATH`. Drive it from there:

```bash
magpie auth login                            # browser device flow
magpie feed create                           # opens $EDITOR on a feed template (sources + retention)
magpie watch create                          # opens $EDITOR on a watch template (feeds + action chain)
magpie activity summary --action <action_id> # per-state run breakdown for any action (filter, webhook, log)
```

On a headless box (a server you SSH into, no browser), skip the device flow and use a personal access token. Mint one on the server, then sign in with it on the box, the token is pasted (stdin or a hidden prompt, never the command line) and stored in `~/.magpie` at `0600`, persisting across sessions:

```bash
# on the server (the issue_cli_token management command, via the local stack):
make local-manage CMD="issue_cli_token --email you@example.com --name my-box"
# then, on the box:
magpie auth login --token   # paste the printed token at the prompt
```

For CI or an ephemeral box, set `MAGPIE_TOKEN=mgp_...` in the environment instead: it's
read on every request, takes precedence over a stored login, and is never persisted (the
`GH_TOKEN` pattern), so no login step. Manage tokens with `magpie auth token list` /
`create` / `revoke` (minting needs a browser login; a token can't mint another).

A watch's `actions:` chain typically starts with a `semantic_filter` (your natural-language criteria + threshold) followed by a `webhook` or `log` delivery. Pick a backfill window when you create the feed and the first `make local-tick` scores real posts against your criteria immediately, with no wait for the scheduler.

Full command list: the [magpie CLI reference](apps/cli/README.md). The dev loop runs through `make`: see [make/README.md](make/README.md) or `make help`.

### Running it continuously

`make local-tick` runs one pass by hand. For ongoing operation, start the background scheduler. The four pipeline stages each tick on their own cadence (poll feeds, trigger watches, drain runs, flush digests):

```bash
make up-jobs                  # start the tickers (a pid + log per stage under .jobs/)
tail -f .jobs/drain.log       # watch a stage
make down-jobs                # stop them
```

Each stage is single-flight: a pass that outruns its interval self-skips the next tick, so loops never stack. Production scheduling is then just a plain cron entry per command on the same cadences, with no flock or singleton infrastructure. Override any cadence inline, e.g. `make up-jobs DRAIN_INTERVAL=30`.

Run `make help` for the full target list (`make up` / `down`, `make logs`, `make local-test`, `make local-check`, and so on).

## How it works

A `Feed` is a reusable, curated stream (a set of sources plus an item log). A `Watch` subscribes to one or more feeds and runs an ordered **action chain** over each new item: a `semantic_filter` gates the chain (a score below threshold stops it), and downstream `webhook` / `log` actions deliver what passes. One feed can back many watches, so you pay for source polling once.

```mermaid
graph TD
    subgraph Sources
        REDDIT[Reddit]
        RSS[RSS / Atom feeds]
        HN[Hacker News]
        SLACK[Slack]
        LINKEDIN[LinkedIn]
        GITHUB[GitHub]
    end

    subgraph OpenMagpie
        FEED[Feed<br/>curated streams + item log]
        WATCH[Watch<br/>subscribes to feeds]
        FILTER[semantic_filter<br/>action]
        ENGINE[Relevance engine<br/>BYO LLM]
        DELIVER[webhook / log<br/>delivery action]
    end

    subgraph Out
        WEBHOOK[Webhook]
        LOG[Log]
        FUTURE["email / Slack (planned)"]
    end

    REDDIT --> FEED
    RSS --> FEED
    HN --> FEED
    SLACK -. planned .-> FEED
    LINKEDIN -. planned .-> FEED
    GITHUB -. planned .-> FEED

    FEED -- "new items" --> WATCH
    WATCH -- "action chain" --> FILTER
    FILTER --> ENGINE
    ENGINE -. "your LLM" .-> LLM["any OpenAI-compatible /v1 API<br/>Ollama | vLLM | llama.cpp | LM Studio | OpenAI"]
    FILTER -- "passes -> next action" --> DELIVER

    DELIVER --> WEBHOOK
    DELIVER --> LOG
    DELIVER -. planned .-> FUTURE
```

Delivery is **instant** (per item) or **digest** (a window of items batched into one emission). A `webhook` action POSTs (or PUTs / PATCHes) one self-describing body; instant and digest use the same shape (instant is a one-item batch):

```json
{
  "watch": {"id": "01K...", "name": "ai-webhook"},
  "action_id": "01K...",
  "delivery": "digest",
  "window": {"since": "...", "until": "..."},
  "items": [
    {
      "key": "reddit_subreddit:abc123",
      "source": {"label": "r/ClaudeAI", "kind": "reddit_subreddit"},
      "item": {"title": "...", "url": "..."}
    }
  ]
}
```

`item` is the feed item narrowed to the action's `include_fields`. Each item's `key` is `source:external_id`; delivery is at-least-once, so receivers dedup on it. Every call is recorded as a `WatchActionDelivery` you can inspect:

```bash
make local-cli ARGS="delivery list --action <webhook_action_id>"    # the list: state / HTTP / host / items / attempt
make local-cli ARGS="delivery get <delivery_id>"                    # one call in full, incl. the exact body sent
```

See [AGENTS.md](AGENTS.md) for the design conventions (char pointers, typed-blob pattern, the trigger/drain/flush execution model).

## Why self-host it

Social listening is a crowded market (Brand24, Mention, Octolens, Syften, and tools like OutX that pair monitoring with AI-drafted replies). They are all closed SaaS behind a paid plan, a trial, or a sales demo, and the few genuinely free options are basic mention notifiers, not full listening. OpenMagpie is the open, self-hostable exception: run it on your own box with your own model for the cost of the hardware.

- **Open source.** Apache 2.0, the whole stack. Read it, fork it, and extend the connectors and engines yourself.
- **Bring your own LLM.** Relevance is judged by an LLM you run (via any OpenAI-compatible backend like Ollama, vLLM, llama.cpp, LM Studio etc), so your criteria and your matches stay on your infrastructure when you self-host the model.
- **Natural-language matching.** You describe what's relevant in natural language and the model scores each new post on meaning.
- **Auditable.** Every poll, judgement, and delivery is a row you can inspect (`magpie activity summary` / `delivery list`), as a table or `--jsonl` to pipe into `jq` / an LLM, or written to a file with `-o`.

## What's shipped today

| Layer | Shipped |
|---|---|
| Connectors | Reddit (`reddit_subreddit`), Hacker News (`hn_feed`, `hn_comment`), RSS/Atom (`rss`) |
| Engines | Any OpenAI-compatible `/v1` API: Ollama, vLLM, llama.cpp, LM Studio, OpenAI, ... |
| Action kinds | `semantic_filter` (LLM-judged), `webhook`, `log` |
| Delivery modes | instant, digest |
| Webhook methods | `POST`, `PUT`, `PATCH` |
| Delivery audit | per-attempt `WatchActionDelivery` |

## Roadmap

- **More connectors**: Slack, LinkedIn, GitHub, Bluesky, Mastodon, and X.
- **More engines**: Anthropic, OpenAI, and a keyword engine behind the same `Engine` Protocol.
- **Learns from feedback**: thumbs up/down on past matches become few-shot examples for the next pass.
- **Run-history in the payload**: the upstream filter score and chain provenance as an opt-in webhook field.
- **Branching and parallel chains**: the data model already carries `WatchPath` and dense action ranks; multi-path and DAG branching are post-v1.
- **Retention**: pruning for `WatchActionRun` and `WatchActionDelivery` history.

## Hosted version

Self-hosting is free and stays free. A managed hosted version (no infrastructure to run) is in the works as the paid tier.

Join the waitlist at [openmagpie.ai](https://www.openmagpie.ai/).

## Project structure

uv workspace; one root `uv.lock` for everything Python.

```
apps/
  core/                       Django backend (deployable)
    common/                   BaseModel (ULID PK + timestamps), ULIDField, locks, db ceilings, /healthz
    accounts/                 User / Account / UserProfile + services + AccountScopedAPIView mixin
    auth_api/                 signup / login / logout / me + tokens/* + device-flow handshake (DRF)
    sources/                  Connectors (Reddit, Hacker News, RSS/Atom) + SourcePayload classes + registry
    feeds/                    Feed + Source + FeedItem models + poll orchestrator + item log
    engine/                   Engine Protocol + OpenAICompatEngine + registry (+ probe)
    watches/                  Watch + WatchFeed + WatchPath + WatchAction + WatchActionRun + WatchActionDelivery
    conf/                     settings (base/local), urls, wsgi
  cli/                        magpie CLI (Typer + httpx + Pydantic); distributed as a standalone wheel
packages/
  openmagpie-schema/          Pure Pydantic models shared by core + cli (configs, wire types, feed shapes)
web/                          pnpm workspace: apps/{app,marketing,email-render} (Next.js) + packages/{ui,api-utils,auth,tailwind-config}
make/                         Per-concern Makefile targets
scripts/                      quickstart installer (quickstart/{bootstrap,preflight,run,seed,tick}.sh) + dev tooling (Docker preflight, git hooks, whitespace/branch/length checks, make-help)
```

## Documentation

- [CONTRIBUTING.md](CONTRIBUTING.md): contribution flow, branch naming, running the checks.
- [CHANGELOG.md](CHANGELOG.md): notable changes per release.
- [magpie CLI reference](apps/cli/README.md): install + the full command list.
- [make/README.md](make/README.md): the important dev `make` commands (`make help` for the full list).
- [AGENTS.md](AGENTS.md): cross-cutting design conventions, plus per-area notes: [apps/core](apps/core/AGENTS.md), [apps/cli](apps/cli/AGENTS.md), [web](web/AGENTS.md).

## Telemetry

OpenMagpie ships **anonymous, opt-in** usage telemetry, **off by default**. It helps prioritize what to build (a UI? which sources next? is setup too hard?) without ever sending your content. Enable it during `quickstart`, or with `make local-manage CMD="telemetry enable"`; turn it off any time with `make local-manage CMD="telemetry disable"` or `DO_NOT_TRACK=1`. Exactly what is and isn't collected: [apps/core/TELEMETRY.md](apps/core/TELEMETRY.md).

## License

OpenMagpie is open source under the [Apache License 2.0](LICENSE), with optional enterprise directories (`**/ee/`) reserved for future commercial features.
