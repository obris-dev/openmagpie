# Changelog

## [0.6.0](https://github.com/obris-dev/openmagpie/compare/cli-v0.5.0...cli-v0.6.0) (2026-07-01)


### Features

* **auth:** AuthUser as a shared contract across server, CLI, and web ([#154](https://github.com/obris-dev/openmagpie/issues/154)) ([beeaa99](https://github.com/obris-dev/openmagpie/commit/beeaa993f6e97071931376719e012219d7525c23))
  * **Clearer errors when the server and CLI disagree**: a response this CLI can't parse (the two are on incompatible versions) now prints "the server and CLI are on incompatible versions, update magpie" instead of a raw Python traceback, on `auth login`, `auth status`, and the token / device-flow sign-in paths.
  * **Readable config errors + corrupt-config handling**: a bad action config on `watch action add` / `watch action edit` now lists the offending fields (for example a missing `instructions`) instead of a generic failure, and `watch action get` / `watch edit` render an unreadable (corrupt) stored config as `null` and flag it rather than crashing.
* **schema:** generate + guard the cross-boundary JSON Schema ([#151](https://github.com/obris-dev/openmagpie/issues/151)) ([4b57414](https://github.com/obris-dev/openmagpie/commit/4b5741492b18aae2de0aa4ee3d9f1f7090da0c25))
  * The CLI validates API responses against the same contract the server generates from its models (no hand-maintained copy), so a shape mismatch surfaces explicitly instead of silently mis-parsing.

## [0.5.0](https://github.com/obris-dev/openmagpie/compare/cli-v0.4.1...cli-v0.5.0) (2026-06-30)


### Features

* pause and resume feeds and watches ([#147](https://github.com/obris-dev/openmagpie/issues/147)) ([39f8f85](https://github.com/obris-dev/openmagpie/commit/39f8f85af8a612f84f3c137c66fa0bbcff123144))
  * **`magpie feed pause | resume <id>`** and **`magpie watch pause | resume <id>`**: pause a feed or watch to stop it without tearing it down (a paused feed stops pulling new items; a paused watch stops acting on them), then resume when you want it running again. The `ACTIVE` column in `feed list` / `watch list` and the title in `feed get` / `watch get` show the current state.
  * `feed edit` and `watch edit` now warn when a full-replace edit from a file would silently un-pause a paused resource (an `-f` file that omits `is_active` defaults it back to active), and point you at the dedicated `resume` command instead.

## [0.4.1](https://github.com/obris-dev/openmagpie/compare/cli-v0.4.0...cli-v0.4.1) (2026-06-30)


### Bug Fixes

* **telemetry:** make anonymous telemetry opt-out (Next.js-style) ([#144](https://github.com/obris-dev/openmagpie/issues/144)) ([e8c1f68](https://github.com/obris-dev/openmagpie/commit/e8c1f6858a6f359fbe0f26e5c84332eb478518db))
  * Telemetry is now on by default (opt-out). The post-login opt-in prompt is replaced by a one-time disclosure notice (no `y/N`), and `magpie telemetry status` now reports whether it's actually emitting, not just the mode.
  * Turn it off any time: `magpie telemetry disable` (or `DO_NOT_TRACK=1`).

## [0.4.0](https://github.com/obris-dev/openmagpie/compare/cli-v0.3.3...cli-v0.4.0) (2026-06-29)


### Features

* extract action (hydrate runs) + magpie activity export ([#137](https://github.com/obris-dev/openmagpie/issues/137)) ([e1d7e08](https://github.com/obris-dev/openmagpie/commit/e1d7e08430783224d70fefd3be5c016834980459))
  * **`magpie activity export`**: export a watch action's runs to a file (CSV by default, or `--jsonl`), so you can take a watch's output into a spreadsheet, an LLM, or a script. The columns match the action: an `extract` action gives you the extracted fields (one row per item), a relevance filter gives the score and reason, a log gives the delivered line. Scope it to a time range with `--occurred-*` / `--completed-*` (a duration like `7d` or an exact date); with no window it covers the last 7 days.
  * **`extract` action support**: create the new extract action with `magpie watch action add` (`--rank` places it in the chain), and see an extract run's pulled fields in `magpie activity get`.

## [0.3.3](https://github.com/obris-dev/openmagpie/compare/cli-v0.3.2...cli-v0.3.3) (2026-06-28)


### Bug Fixes

* **cli:** auth-login UX for headless / no-browser sign-in ([#138](https://github.com/obris-dev/openmagpie/issues/138)) ([120edda](https://github.com/obris-dev/openmagpie/commit/120edda72a9205b2dfd15ad23654cd58cfaf3954))

## [0.3.2](https://github.com/obris-dev/openmagpie/compare/cli-v0.3.1...cli-v0.3.2) (2026-06-24)


### Bug Fixes

* **cli:** `watch action add`/`edit` now preview the change and prompt before applying, with `--dry-run` (validate + show the result, then stop) and `--yes` (skip the prompt; required when piped, for automation) - the flow `watch edit`/`feed edit` already have. And `watch edit` now warns when your file omits an existing action's `id`, since that drops and recreates the action (its pending activity won't complete); it lists the affected ids and points you to `watch action edit`. ([#133](https://github.com/obris-dev/openmagpie/issues/133)) ([0d4cd29](https://github.com/obris-dev/openmagpie/commit/0d4cd29b84648dbb7a5d97327ebb1e90241502f1))

## [0.3.1](https://github.com/obris-dev/openmagpie/compare/cli-v0.3.0...cli-v0.3.1) (2026-06-24)


### Bug Fixes

* **cli:** `feed edit` now warns when your file's `sources:` differ from the feed's current set. `feed edit` only changes feed-level config and silently drops `sources`, so it points you to `feed source set` to actually apply them; no warning when they already match. ([#130](https://github.com/obris-dev/openmagpie/issues/130)) ([d02ace9](https://github.com/obris-dev/openmagpie/commit/d02ace997b10e4f3d5f05785956c4570c4f36a6c))

## [0.3.0](https://github.com/obris-dev/openmagpie/compare/cli-v0.2.0...cli-v0.3.0) (2026-06-23)


### Features

* **telemetry:** `magpie telemetry enable | disable | status` + a post-login opt-in prompt; tags requests with `X-Magpie-Surface: cli` ([#123](https://github.com/obris-dev/openmagpie/issues/123)) ([8b08f66](https://github.com/obris-dev/openmagpie/commit/8b08f66e46737b8fabe5c5ce676dbcbe5991a6f5))

## [0.2.0](https://github.com/obris-dev/openmagpie/compare/cli-v0.1.0...cli-v0.2.0) (2026-06-21)


### Features

* `activity get` now surfaces linked-article enrichment status for semantic-filter runs, so you can see whether a match was judged with the fetched article. ([#112](https://github.com/obris-dev/openmagpie/issues/112))

## 0.1.0 (2026-06-16)


### Features

* **auth:** personal access tokens for headless CLI login ([#85](https://github.com/obris-dev/openmagpie/issues/85)) ([cdc349f](https://github.com/obris-dev/openmagpie/commit/cdc349f74dd1f86e7e35ffbf8284c8205aa93f95))
* CLI information-architecture migration (integration branch) ([#76](https://github.com/obris-dev/openmagpie/issues/76)) ([adb2b87](https://github.com/obris-dev/openmagpie/commit/adb2b8717a7f19cea96437323436c07bd9e42956))
* **cli:** magpie quickstart wizard + /v1/engines status endpoint ([064685f](https://github.com/obris-dev/openmagpie/commit/064685f657a81ea2bf34fe75a1e4dfb55fe2661c))
* **cli:** magpie quickstart wizard + /v1/engines status endpoint ([413e7db](https://github.com/obris-dev/openmagpie/commit/413e7db3c86893597107050a296d35536f7c5603))
* **cli:** pivoted FIELD|VALUE tables for feed get + watch get ([e233900](https://github.com/obris-dev/openmagpie/commit/e23390099c1cf7d7c414a2e576091650d8dcf496))
* **cli:** render feed get as a pivoted FIELD|VALUE table ([d441123](https://github.com/obris-dev/openmagpie/commit/d44112340674d7221f82f3a476f42504ee05ad29))
* **cli:** render watch get as a pivoted FIELD|VALUE table ([66a3a74](https://github.com/obris-dev/openmagpie/commit/66a3a74ddb44ef54606519032131b506a776c49e))
* **cli:** shared aligned table + column headers for every list view ([5925bf2](https://github.com/obris-dev/openmagpie/commit/5925bf2154ee637efe007bc6fdddf5f96cdbbc5e))
* **cli:** shared aligned table for every list view ([35ba2ad](https://github.com/obris-dev/openmagpie/commit/35ba2ad9e234b1b4b6051ac52afe7a3384d5874f))
* **cli:** vendor openmagpie-schema into the wheel for standalone install ([76b3be5](https://github.com/obris-dev/openmagpie/commit/76b3be5301dcdfc6a788511ff06e66d2601de718))
* **cli:** vendor openmagpie-schema into the wheel for standalone install ([82e85e4](https://github.com/obris-dev/openmagpie/commit/82e85e43f05a66ae6496bcf989d813c88bf63804))
* **cli:** watch action deliveries view ([de423d6](https://github.com/obris-dev/openmagpie/commit/de423d6542a92d36d0a2607c87ba2e76411dfda0))
* **engine:** generalize relevance engine to any OpenAI-compatible LLM ([#84](https://github.com/obris-dev/openmagpie/issues/84)) ([05767d9](https://github.com/obris-dev/openmagpie/commit/05767d9d86bdff752e9e74c748e6a585db9a4ec5))
* **engine:** per-kind how-to hint on EngineStatus ([edaa6a8](https://github.com/obris-dev/openmagpie/commit/edaa6a8c3f51b316ad3855a4615efb5b3e1450d4))
* **feeds + listeners:** create-time watermark, backfill UX, judge ETA ([4e492f0](https://github.com/obris-dev/openmagpie/commit/4e492f0879b4c9fcf65bf67705e9009fffd3fc3a))
* **feeds + listeners:** create-time watermark, backfill UX, judge ETA ([dffcdb7](https://github.com/obris-dev/openmagpie/commit/dffcdb72284ca1e53474f0317d4beebcaec3ba55))
* **feeds:** Feed primitive + Listener subscription model (v1, no backward compat) ([1964637](https://github.com/obris-dev/openmagpie/commit/19646376862ece229aeb266f2271cfe9e3c45bec))
* **feeds:** Feed primitive + Listener subscription model (v1, no backward compat) ([180b72a](https://github.com/obris-dev/openmagpie/commit/180b72af142deab58f97dc267ed219fc7534beb1))
* **feeds:** Source rows + magpie feed source verbs ([722379f](https://github.com/obris-dev/openmagpie/commit/722379fb23f7e724a5c55588135423e428bb0cdf))
* **listener:** relevance_score, rewind, payload-sample ([eee5883](https://github.com/obris-dev/openmagpie/commit/eee5883d315fb43da2aabbd03648139362f8cabd))
* **listener:** relevance_score, rewind, payload-sample (+ scoping mixins, preview service) ([48cdfd8](https://github.com/obris-dev/openmagpie/commit/48cdfd8f4119cbd2894ba046f2923f5e39fdabf4))
* **listeners:** magpie listener hits — paginated review + JSON / CSV ([774ca7d](https://github.com/obris-dev/openmagpie/commit/774ca7d8aa679288a3e87c7b824e4a3d9c7fbb51))
* one-command quickstart seed (make quickstart -&gt; a real match) ([#69](https://github.com/obris-dev/openmagpie/issues/69)) ([00cac92](https://github.com/obris-dev/openmagpie/commit/00cac92979eb59879973c61b34f7d9bf49f09e04))
* **rss:** challenge-bypass sidecar fallback + connector hardening ([e2bec0c](https://github.com/obris-dev/openmagpie/commit/e2bec0c4d9460ebe64b17ae71fd6dd1de5cec79e))
* **rss:** FlareSolverr sidecar for Cloudflare-challenge fallback ([7740263](https://github.com/obris-dev/openmagpie/commit/7740263a9cf31a60d63e348dc3a87743349531f2))
* **sources:** flatten CuratedFeedConfig.streams into a Source table ([f028618](https://github.com/obris-dev/openmagpie/commit/f028618e9b4608435a99516d02eac91bfb1e5b27))
* **v2:** delivery actions (webhook + log) + stable action ids ([caa0144](https://github.com/obris-dev/openmagpie/commit/caa0144c94e120e7c4ac666d282273eb6428ab07))
* **v2:** relocate SourcePayload, drop Listener / Event / notifier code ([37a07cd](https://github.com/obris-dev/openmagpie/commit/37a07cd15e0d60872efb94d2f99814a572ff675c))
* **v2:** relocate SourcePayload, drop Listener / Event / notifier code ([72ebfb7](https://github.com/obris-dev/openmagpie/commit/72ebfb71c1cf3b1d7953c3ad7c4cc77d9a953442))
* **v2:** watch action activity — run audit-log endpoint + CLI ([5e73222](https://github.com/obris-dev/openmagpie/commit/5e73222abdeb0976e972ca5256519e0969f4bab3))
* **v2:** watch action activity — run audit-log endpoint + CLI ([8d9cc2e](https://github.com/obris-dev/openmagpie/commit/8d9cc2e338b0bb56ce69ee248ac53efb748ebee1))
* **v2:** Watch CRUD vertical slice — schema + server API + magpie watch CLI ([d03914e](https://github.com/obris-dev/openmagpie/commit/d03914e091a0de33957b7a42d0af721f730bf4d8))
* **v2:** Watch CRUD vertical slice — schema + server API + magpie watch CLI ([8e91a18](https://github.com/obris-dev/openmagpie/commit/8e91a18fa7af0df76907f0f72227f87b766f1c30))
* **v2:** WebhookAction + LogAction (delivery actions) ([c0c8005](https://github.com/obris-dev/openmagpie/commit/c0c80057dd3132c3de4a26c02ce4efcc9dcf9df0))
* **watches:** activity summary by window (evaluation-time) + richer run rows ([bf5f12f](https://github.com/obris-dev/openmagpie/commit/bf5f12f917c2a23fcef42e39be2aad10c9db30c5))
* **watches:** address actions by id alone (leaf-only CLI + flat routes) ([b4274ab](https://github.com/obris-dev/openmagpie/commit/b4274abd6f682294051db097dc51a74c01c8c216))
* **watches:** leaf-only action CLI + windowed activity summary ([ca7fa6a](https://github.com/obris-dev/openmagpie/commit/ca7fa6aac84e4715aa641fb2478f6981b58b6bd3))


### Bug Fixes

* **cli:** render last_polled_at via isoformat; note policy duck-typing ([77bc4a0](https://github.com/obris-dev/openmagpie/commit/77bc4a0af6ab64e9f48f52c9819705f55f0eec5f))
* **feeds:** dry-run create reports the would-be source count ([7de4f1f](https://github.com/obris-dev/openmagpie/commit/7de4f1f2d964160fb554b4952114677b2341579a))
* **feeds:** dry-run create reports the would-be source count ([3e1d7f0](https://github.com/obris-dev/openmagpie/commit/3e1d7f0dffe9b92b89fe3b0864d62eee9b99769f))


### Documentation

* **readme:** point at magpie quickstart + em-dash scrub ([aad8af5](https://github.com/obris-dev/openmagpie/commit/aad8af505a740b62cab2a647d086f6ddd2e68f03))
* **readme:** point at magpie quickstart + em-dash scrub ([a51d6c2](https://github.com/obris-dev/openmagpie/commit/a51d6c2723e5b700a4e09e0093a980287c95bd98))
* **readme:** pre-public fixes + document the background scheduler ([efeabe3](https://github.com/obris-dev/openmagpie/commit/efeabe31e50093475fdd85b490b516ad41265ab6))
* **readme:** reference the CLI README + AGENTS docs; fix CLI activity arity ([8ba3e58](https://github.com/obris-dev/openmagpie/commit/8ba3e5856c0d550ea4dd92604a0ffaa8037522fe))
* **v2:** rewrite core AGENTS.md for watches; refresh root + cli docs ([ae7efd5](https://github.com/obris-dev/openmagpie/commit/ae7efd584236e98e9788d466ecee324b7b05ab5f))
* **v2:** rewrite core AGENTS.md for watches; refresh root + cli docs ([c43f70f](https://github.com/obris-dev/openmagpie/commit/c43f70f817918f653d477ce49ea4d1c1a398d7fd))
* VHS-recorded CLI tour as the README hero ([#86](https://github.com/obris-dev/openmagpie/issues/86)) ([1fcb843](https://github.com/obris-dev/openmagpie/commit/1fcb843ac27613190a814f71317ba5ac1c227eed))
