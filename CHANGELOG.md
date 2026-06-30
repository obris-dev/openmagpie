# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0](https://github.com/obris-dev/openmagpie/compare/v0.4.1...v0.5.0) (2026-06-30)


### Features

* pause and resume feeds and watches ([#147](https://github.com/obris-dev/openmagpie/issues/147)) ([39f8f85](https://github.com/obris-dev/openmagpie/commit/39f8f85af8a612f84f3c137c66fa0bbcff123144))
  * **Pause and resume feeds and watches**: a feed or watch can now be paused and later resumed, rather than deleted and rebuilt, when you want to stop it for a while. A paused feed stops polling its sources for new items; a paused watch stops running its action chain on incoming items. Resuming picks the work back up. The toggle is a lightweight `PATCH /v1/feeds/<id>` or `PATCH /v1/watches/<id>` with `{"is_active": false}` to pause and `true` to resume, and it flips only the active flag, so a watch's action chain and a feed's source list are left untouched (unlike a full edit, which replaces them). A feed can also be created already paused.

## [0.4.1](https://github.com/obris-dev/openmagpie/compare/v0.4.0...v0.4.1) (2026-06-30)


### Bug Fixes

* **telemetry:** make anonymous telemetry opt-out (Next.js-style) ([#144](https://github.com/obris-dev/openmagpie/issues/144)) ([e8c1f68](https://github.com/obris-dev/openmagpie/commit/e8c1f6858a6f359fbe0f26e5c84332eb478518db))
  * Anonymous usage telemetry is now **on by default** (opt-out), rather than only after an explicit opt-in. It stays anonymous (a random per-install id, never your account or content) and is easy to turn off: `telemetry disable`, `DO_NOT_TRACK=1`, or an empty `POSTHOG_API_KEY`.
  * **Upgrading:** an existing install left on the default starts emitting after this upgrade with no action on your part; opt out with any of the above. Full details in `apps/core/TELEMETRY.md`.

## [0.4.0](https://github.com/obris-dev/openmagpie/compare/v0.3.4...v0.4.0) (2026-06-29)


### Features

* extract action (hydrate runs) + magpie activity export ([#137](https://github.com/obris-dev/openmagpie/issues/137)) ([e1d7e08](https://github.com/obris-dev/openmagpie/commit/e1d7e08430783224d70fefd3be5c016834980459))
  * **`extract` action kind**: a watch action that uses the LLM to pull a set of fields you declare (each a `name` + a plain-language `description`) out of every item the watch matches, and records them on the item's run. It turns fuzzy incoming content (a headline, a post) into structured data you can report on. It only enriches (it never drops items), so it runs after a relevance filter rather than in place of one.
  * **Activity time windows**: you can now scope a watch's activity to a time range, by when an item was published (`occurred`) or when its run finished (`completed`), given as either a duration like `7d` or an exact date. Lets a view or export cover, say, just the last 30 days.

## [0.3.4](https://github.com/obris-dev/openmagpie/compare/v0.3.3...v0.3.4) (2026-06-24)


### Bug Fixes

* **watches:** the single-action endpoints (`POST /v1/watches/<id>/actions`, `PUT /v1/actions/<id>`) now accept `?dry_run=true` - validate and return the would-be action without persisting (mirroring the whole-watch create/edit dry-run), so a client can preview a single-action add or edit before applying. ([#133](https://github.com/obris-dev/openmagpie/issues/133)) ([0d4cd29](https://github.com/obris-dev/openmagpie/commit/0d4cd29b84648dbb7a5d97327ebb1e90241502f1))

## [0.3.3](https://github.com/obris-dev/openmagpie/compare/v0.3.2...v0.3.3) (2026-06-24)


### Changed

* **schema/core:** `spec_hash` now derives from a shared `canonical_spec` source-identity used by both the server and the magpie CLI — an internal refactor with byte-identical hashes and no behavior change (shipped alongside the CLI's `feed edit` sources-diff warning). ([#130](https://github.com/obris-dev/openmagpie/issues/130)) ([d02ace9](https://github.com/obris-dev/openmagpie/commit/d02ace997b10e4f3d5f05785956c4570c4f36a6c))

## [0.3.2](https://github.com/obris-dev/openmagpie/compare/v0.3.1...v0.3.2) (2026-06-23)


### Bug Fixes

* **sources:** when a Reddit poll is rate-limited (429), wait exactly as long as Reddit's `x-ratelimit-reset` header asks instead of guessing exponential backoff — so the retry lands right when the ~40s window reopens rather than hammering it while it's still closed. ([#128](https://github.com/obris-dev/openmagpie/issues/128)) ([60d54cb](https://github.com/obris-dev/openmagpie/commit/60d54cb759bc8dab4b1123980868c5a4841d0b92))

## [0.3.1](https://github.com/obris-dev/openmagpie/compare/v0.3.0...v0.3.1) (2026-06-23)


### Performance Improvements

* **reddit:** poll all of a feed's subreddit sources in one combined `/r/a+b+c/new.rss` request instead of one per sub — cuts Reddit calls per poll cycle from N to ~1, ending the per-IP rate-limit (429) storms a many-subreddit feed used to hit. Each source keeps its own watermark, and case-only-variant subreddits (`r/Python` ≡ `r/python`) collapse to a single source. ([#126](https://github.com/obris-dev/openmagpie/issues/126)) ([5c8e811](https://github.com/obris-dev/openmagpie/commit/5c8e811d6f87022afe840918154c566fb75f00bf))

## [0.3.0](https://github.com/obris-dev/openmagpie/compare/v0.2.0...v0.3.0) (2026-06-23)


### Features

* **telemetry:** anonymous, opt-in product telemetry — off by default, owner-consented, never sends your content; PostHog Cloud (US); milestone events + a daily rolled-up heartbeat ([#123](https://github.com/obris-dev/openmagpie/issues/123)) ([8b08f66](https://github.com/obris-dev/openmagpie/commit/8b08f66e46737b8fabe5c5ce676dbcbe5991a6f5))

## [0.2.0](https://github.com/obris-dev/openmagpie/compare/v0.1.0...v0.2.0) (2026-06-21)


### Features

* **Hacker News connectors** (`hn_feed`, `hn_comment`): listen to HN story feeds (new / Show HN / Ask HN) and a keyword-filtered slice of the comment stream via the Algolia API. ([#112](https://github.com/obris-dev/openmagpie/issues/112))
* **Linked-article enrichment:** the semantic filter now fetches a post's linked article and scores relevance on its content, not just the title (opt out with `fetch_external_content: false`); outbound fetches are SSRF-contained behind a pinned-IP transport. ([#112](https://github.com/obris-dev/openmagpie/issues/112))

## 0.1.0 (2026-06-16)


### Features

* **auth:** personal access tokens for headless CLI login ([#85](https://github.com/obris-dev/openmagpie/issues/85)) ([cdc349f](https://github.com/obris-dev/openmagpie/commit/cdc349f74dd1f86e7e35ffbf8284c8205aa93f95))
* **auth:** web auth UI + magpie CLI + device-flow handshake ([762f366](https://github.com/obris-dev/openmagpie/commit/762f36601c7d55eedd67962d87816f5e759925c9))
* **auth:** web auth UI + magpie CLI + device-flow handshake ([c482818](https://github.com/obris-dev/openmagpie/commit/c4828187319f2c08cca484dfbda0843310612f02))
* CLI information-architecture migration (integration branch) ([#76](https://github.com/obris-dev/openmagpie/issues/76)) ([adb2b87](https://github.com/obris-dev/openmagpie/commit/adb2b8717a7f19cea96437323436c07bd9e42956))
* **cli:** magpie listener create + list + template ([16b531b](https://github.com/obris-dev/openmagpie/commit/16b531b7e18fd1f9be6be791c619841a1332c416))
* **cli:** magpie quickstart wizard + /v1/engines status endpoint ([064685f](https://github.com/obris-dev/openmagpie/commit/064685f657a81ea2bf34fe75a1e4dfb55fe2661c))
* **cli:** magpie quickstart wizard + /v1/engines status endpoint ([413e7db](https://github.com/obris-dev/openmagpie/commit/413e7db3c86893597107050a296d35536f7c5603))
* **cli:** vendor openmagpie-schema into the wheel for standalone install ([76b3be5](https://github.com/obris-dev/openmagpie/commit/76b3be5301dcdfc6a788511ff06e66d2601de718))
* **cli:** vendor openmagpie-schema into the wheel for standalone install ([82e85e4](https://github.com/obris-dev/openmagpie/commit/82e85e43f05a66ae6496bcf989d813c88bf63804))
* **cli:** watch action deliveries view ([de423d6](https://github.com/obris-dev/openmagpie/commit/de423d6542a92d36d0a2607c87ba2e76411dfda0))
* **engine:** generalize relevance engine to any OpenAI-compatible LLM ([#84](https://github.com/obris-dev/openmagpie/issues/84)) ([05767d9](https://github.com/obris-dev/openmagpie/commit/05767d9d86bdff752e9e74c748e6a585db9a4ec5))
* **engine:** per-kind how-to hint on EngineStatus ([edaa6a8](https://github.com/obris-dev/openmagpie/commit/edaa6a8c3f51b316ad3855a4615efb5b3e1450d4))
* **engine:** per-listener model override + engine-owned validation ([cccc7f4](https://github.com/obris-dev/openmagpie/commit/cccc7f451de2a013e835ae41b37b4696d9bbe888))
* **engine:** per-listener model override + Ollama availability check ([53d9ed1](https://github.com/obris-dev/openmagpie/commit/53d9ed1df9125400feaab94298f6e66a487b7c7f))
* **feeds + listeners:** create-time watermark, backfill UX, judge ETA ([4e492f0](https://github.com/obris-dev/openmagpie/commit/4e492f0879b4c9fcf65bf67705e9009fffd3fc3a))
* **feeds + listeners:** create-time watermark, backfill UX, judge ETA ([dffcdb7](https://github.com/obris-dev/openmagpie/commit/dffcdb72284ca1e53474f0317d4beebcaec3ba55))
* **feeds:** Feed primitive + Listener subscription model (v1, no backward compat) ([1964637](https://github.com/obris-dev/openmagpie/commit/19646376862ece229aeb266f2271cfe9e3c45bec))
* **feeds:** Feed primitive + Listener subscription model (v1, no backward compat) ([180b72a](https://github.com/obris-dev/openmagpie/commit/180b72af142deab58f97dc267ed219fc7534beb1))
* **feeds:** renewable poll lock lease so any feed size polls reliably ([6d828f2](https://github.com/obris-dev/openmagpie/commit/6d828f2d502f4401cba1e9a3b97e0b735b9e70f0))
* **feeds:** renewable poll-lock lease so any feed size polls reliably ([71656a7](https://github.com/obris-dev/openmagpie/commit/71656a797b97b422f8b581cb9dd4b9b2658a55fb))
* **feeds:** Source rows + magpie feed source verbs ([722379f](https://github.com/obris-dev/openmagpie/commit/722379fb23f7e724a5c55588135423e428bb0cdf))
* **judgment:** bounded retry on transient judge failures ([bfd1a8a](https://github.com/obris-dev/openmagpie/commit/bfd1a8ae30a4d248f1ce79e167d8054c4a39ef1a))
* **judgment:** bounded retry on transient judge failures ([3399e97](https://github.com/obris-dev/openmagpie/commit/3399e976cf4efd0a0ce5bd079b3c45eeb6e801d7))
* **listener:** relevance_score, rewind, payload-sample ([eee5883](https://github.com/obris-dev/openmagpie/commit/eee5883d315fb43da2aabbd03648139362f8cabd))
* **listener:** relevance_score, rewind, payload-sample (+ scoping mixins, preview service) ([48cdfd8](https://github.com/obris-dev/openmagpie/commit/48cdfd8f4119cbd2894ba046f2923f5e39fdabf4))
* **listeners:** dry-run validate + confirm before create ([9de07c3](https://github.com/obris-dev/openmagpie/commit/9de07c34a548d9936e83ddb080ec44bb021aa3d0))
* **listeners:** HTTP API for creating + listing listeners ([206c103](https://github.com/obris-dev/openmagpie/commit/206c103d83fe51788f7dd8576204236fe493f0b1))
* **listeners:** HTTP API for creating + listing listeners ([96c96b5](https://github.com/obris-dev/openmagpie/commit/96c96b5e9d5c89f1c76160b4f77198ff84c13cf5))
* **listeners:** log cold-start watermark init ([bbcd663](https://github.com/obris-dev/openmagpie/commit/bbcd663c348ad37ee40297cd51bc4265a904877d))
* **listeners:** magpie listener hits — paginated review + JSON / CSV ([774ca7d](https://github.com/obris-dev/openmagpie/commit/774ca7d8aa679288a3e87c7b824e4a3d9c7fbb51))
* **listeners:** persist judge cursor incrementally ([cab62a7](https://github.com/obris-dev/openmagpie/commit/cab62a79a2c0b9bb46a3e9fee1e84ff8f5eec70a))
* **listeners:** persist judge cursor incrementally, not just at end ([a349484](https://github.com/obris-dev/openmagpie/commit/a34948462458355ba690e5fefa072bd3d1da9434))
* **listeners:** single-listener get / edit / delete (CRUD) ([cc71fd4](https://github.com/obris-dev/openmagpie/commit/cc71fd488bc6c159f77be5706102001223438110))
* **listeners:** single-listener get / edit / delete (CRUD) ([be05108](https://github.com/obris-dev/openmagpie/commit/be05108350cd01b9428ed8cb9a46435e9d88ce96))
* **marketing:** serve the curl|sh quickstart installer ([#74](https://github.com/obris-dev/openmagpie/issues/74)) ([dfee118](https://github.com/obris-dev/openmagpie/commit/dfee1180b5f69c96a8a9737a053dfed07776ab9f))
* move to Postgres (multi-writer pipeline needs MVCC) ([f4c7775](https://github.com/obris-dev/openmagpie/commit/f4c7775b785a1b1f6223599103f14abfbd557e77))
* move to Postgres (multi-writer pipeline needs MVCC) ([2d25380](https://github.com/obris-dev/openmagpie/commit/2d253808c5a97f753fc9fea7ef6869e6db078114))
* one-command quickstart seed (make quickstart -&gt; a real match) ([#69](https://github.com/obris-dev/openmagpie/issues/69)) ([00cac92](https://github.com/obris-dev/openmagpie/commit/00cac92979eb59879973c61b34f7d9bf49f09e04))
* openmagpie v0 — event-sourced semantic listener ([fc1db1b](https://github.com/obris-dev/openmagpie/commit/fc1db1b0b529cd758662f69a9dedda0195e29a5c))
* openmagpie v0 — event-sourced semantic listener (engine layer) ([a098179](https://github.com/obris-dev/openmagpie/commit/a09817986610b9321c4eb42159bcc2f7b642c514))
* production-deployable images (gunicorn + email-render Dockerfile + GHCR CI) ([#58](https://github.com/obris-dev/openmagpie/issues/58)) ([3e2b815](https://github.com/obris-dev/openmagpie/commit/3e2b81525e9f98bb28706d292d7e5b368f3a56b8))
* **quickstart:** diagnose LLM probe failures + harden uv preflight ([#92](https://github.com/obris-dev/openmagpie/issues/92)) ([ef600a0](https://github.com/obris-dev/openmagpie/commit/ef600a0bcc51941c2dc7347f53414f28ca6e795a))
* **quickstart:** personalized, editable quickstart seed ([#94](https://github.com/obris-dev/openmagpie/issues/94)) ([4695aca](https://github.com/obris-dev/openmagpie/commit/4695aca4330ca0df46154aee5c93445556b95ec7))
* **quickstart:** pin curl|sh install to the latest release tag ([#100](https://github.com/obris-dev/openmagpie/issues/100)) ([ed13302](https://github.com/obris-dev/openmagpie/commit/ed133026405ddc00c04ca18ac3dbe826ed19dbf2))
* **rss:** challenge-bypass sidecar fallback + connector hardening ([e2bec0c](https://github.com/obris-dev/openmagpie/commit/e2bec0c4d9460ebe64b17ae71fd6dd1de5cec79e))
* **rss:** FlareSolverr sidecar for Cloudflare-challenge fallback ([7740263](https://github.com/obris-dev/openmagpie/commit/7740263a9cf31a60d63e348dc3a87743349531f2))
* **schema:** webhook payload contract, HTTP method, delivery wire ([6146d7e](https://github.com/obris-dev/openmagpie/commit/6146d7ecefeb6ad66b41824b113cf03fdddac82d))
* **scripts:** make-free quickstart installer + friendly Docker preflight ([#73](https://github.com/obris-dev/openmagpie/issues/73)) ([9a60544](https://github.com/obris-dev/openmagpie/commit/9a6054417b624ce42b1071f5194d23b55fbe6c77))
* **settings:** cloud settings module + cloud/local env naming ([#57](https://github.com/obris-dev/openmagpie/issues/57)) ([e3b796a](https://github.com/obris-dev/openmagpie/commit/e3b796a802aa7f5679ae5b70465bba12f1803048))
* **sources:** flatten CuratedFeedConfig.streams into a Source table ([f028618](https://github.com/obris-dev/openmagpie/commit/f028618e9b4608435a99516d02eac91bfb1e5b27))
* **sources:** generic RSS / Atom connector ([463f429](https://github.com/obris-dev/openmagpie/commit/463f42971bff87fa34bc2481b7e84ed83decb540))
* **sources:** generic RSS / Atom connector behind `kind: rss` ([b6130a6](https://github.com/obris-dev/openmagpie/commit/b6130a656b99a146062864302d11ed64dceb73d7))
* **sources:** log when a rate-limited page succeeds after retrying ([#93](https://github.com/obris-dev/openmagpie/issues/93)) ([d982b90](https://github.com/obris-dev/openmagpie/commit/d982b903607b3c2efddd38769236afcc0f30aecf))
* transactional email queue + render sidecar (waitlist welcome) ([#56](https://github.com/obris-dev/openmagpie/issues/56)) ([f9b9f80](https://github.com/obris-dev/openmagpie/commit/f9b9f80c0febdfaa3153733bb7bc4ae5e0d531d5))
* **v2:** delivery actions (webhook + log) + stable action ids ([caa0144](https://github.com/obris-dev/openmagpie/commit/caa0144c94e120e7c4ac666d282273eb6428ab07))
* **v2:** digest delivery — fixed-window batched webhook/log ([b6bb208](https://github.com/obris-dev/openmagpie/commit/b6bb2084a64434220d086d1ac1bc1e29e76fc016))
* **v2:** relocate SourcePayload, drop Listener / Event / notifier code ([37a07cd](https://github.com/obris-dev/openmagpie/commit/37a07cd15e0d60872efb94d2f99814a572ff675c))
* **v2:** relocate SourcePayload, drop Listener / Event / notifier code ([72ebfb7](https://github.com/obris-dev/openmagpie/commit/72ebfb71c1cf3b1d7953c3ad7c4cc77d9a953442))
* **v2:** Watch / WatchPath / WatchAction / WatchActionRun / WatchFeed models ([37eaece](https://github.com/obris-dev/openmagpie/commit/37eaece736b74868c931e8c3f373f1aa5cdb23d0))
* **v2:** Watch + WatchPath + WatchAction + WatchActionRun + WatchFeed models ([66e1cc9](https://github.com/obris-dev/openmagpie/commit/66e1cc9614bfd8c5204e0a4b9000a9d092a84183))
* **v2:** watch action activity — run audit-log endpoint + CLI ([5e73222](https://github.com/obris-dev/openmagpie/commit/5e73222abdeb0976e972ca5256519e0969f4bab3))
* **v2:** watch action activity — run audit-log endpoint + CLI ([8d9cc2e](https://github.com/obris-dev/openmagpie/commit/8d9cc2e338b0bb56ce69ee248ac53efb748ebee1))
* **v2:** Watch CRUD vertical slice — schema + server API + magpie watch CLI ([d03914e](https://github.com/obris-dev/openmagpie/commit/d03914e091a0de33957b7a42d0af721f730bf4d8))
* **v2:** Watch CRUD vertical slice — schema + server API + magpie watch CLI ([8e91a18](https://github.com/obris-dev/openmagpie/commit/8e91a18fa7af0df76907f0f72227f87b766f1c30))
* **v2:** watches actually run — action execution + trigger/drain crons ([91f6f65](https://github.com/obris-dev/openmagpie/commit/91f6f65e2b9d554cc67002082dc9022b633558de))
* **v2:** watches actually run — action execution + trigger/drain crons ([b1c480b](https://github.com/obris-dev/openmagpie/commit/b1c480b94cf28cf3adc60e45b7771fd19f85cb7a))
* **v2:** watches digest delivery ([763a3c4](https://github.com/obris-dev/openmagpie/commit/763a3c4e0de2913d46608566ba97a0e171756406))
* **v2:** WebhookAction + LogAction (delivery actions) ([c0c8005](https://github.com/obris-dev/openmagpie/commit/c0c80057dd3132c3de4a26c02ce4efcc9dcf9df0))
* waitlist signup (backend + marketing form + shared toast) ([#55](https://github.com/obris-dev/openmagpie/issues/55)) ([a0591f3](https://github.com/obris-dev/openmagpie/commit/a0591f35713357e4439a961a8512e3cfd40d9118))
* **waitlist:** capture which offering a signup is waiting for ([#63](https://github.com/obris-dev/openmagpie/issues/63)) ([a6bb0dd](https://github.com/obris-dev/openmagpie/commit/a6bb0dd771595faeb04887a32ada0d00756a4eed))
* **waitlist:** most-wanted-source vote (multi-select) on the confirm card ([#65](https://github.com/obris-dev/openmagpie/issues/65)) ([f414994](https://github.com/obris-dev/openmagpie/commit/f4149947c9c957e135bfdb14e7f277290e430ad3))
* **watches:** activity summary by window (evaluation-time) + richer run rows ([bf5f12f](https://github.com/obris-dev/openmagpie/commit/bf5f12f917c2a23fcef42e39be2aad10c9db30c5))
* **watches:** address actions by id alone (leaf-only CLI + flat routes) ([b4274ab](https://github.com/obris-dev/openmagpie/commit/b4274abd6f682294051db097dc51a74c01c8c216))
* **watches:** drain emits periodic per-action progress logs ([36ae0fb](https://github.com/obris-dev/openmagpie/commit/36ae0fb244ad009cb0e70953703dfa3a90943d72))
* **watches:** drain emits periodic per-action progress logs ([9f51ea7](https://github.com/obris-dev/openmagpie/commit/9f51ea7c62b233cd4622141904b514e7af14cfdc))
* **watches:** lead drain line with progress, add action id ([7e5e946](https://github.com/obris-dev/openmagpie/commit/7e5e94659f57492a6afd4d0914be19d1e35649ca))
* **watches:** leaf-only action CLI + windowed activity summary ([ca7fa6a](https://github.com/obris-dev/openmagpie/commit/ca7fa6aac84e4715aa641fb2478f6981b58b6bd3))
* **watches:** per-run progress + ETA on the drain pass ([3a03b87](https://github.com/obris-dev/openmagpie/commit/3a03b87a88e07578bd20e23ad9d5b2fc8a1cd894))
* **watches:** record + dedup webhook deliveries ([151bb5c](https://github.com/obris-dev/openmagpie/commit/151bb5c7018801b4c1c082b42a9186d2a12c8ee2))
* **watches:** unified enriched webhook payload + PUT/PATCH ([5056138](https://github.com/obris-dev/openmagpie/commit/50561384d7eff24f0d99b791fab0d416070ad95a))
* **watches:** WatchActionDelivery model + run.delivery_id ([4138dc1](https://github.com/obris-dev/openmagpie/commit/4138dc18b4abb2bd73bf54bad106771235cd0042))
* **web:** Cloudflare Workers deploy for app + marketing (OpenNext) ([#60](https://github.com/obris-dev/openmagpie/issues/60)) ([3e479be](https://github.com/obris-dev/openmagpie/commit/3e479bebd7ec88eb8f3eaf123a6c9e8931523f9a))
* **web:** marketing landing site (apps/marketing) ([#54](https://github.com/obris-dev/openmagpie/issues/54)) ([9d3f100](https://github.com/obris-dev/openmagpie/commit/9d3f10099a5008aa46e13c91066e1a6712c3ed91))


### Bug Fixes

* **cli:** clear the 2 ty diagnostics ([8cbdeac](https://github.com/obris-dev/openmagpie/commit/8cbdeacfdec69788cc0abb4424ac1b1344e7a413))
* **cli:** fail fast on unmodified template across all input modes ([82fa11c](https://github.com/obris-dev/openmagpie/commit/82fa11ce49993c3bc079065da9027458915c8d81))
* **cli:** fail fast on unmodified template across all input modes ([375795b](https://github.com/obris-dev/openmagpie/commit/375795b601f7bb46c187ad17a639a37b0ad1ce24))
* **cli:** render last_polled_at via isoformat; note policy duck-typing ([77bc4a0](https://github.com/obris-dev/openmagpie/commit/77bc4a0af6ab64e9f48f52c9819705f55f0eec5f))
* **cli:** surface a possibly-created id on unexpected responses ([d824015](https://github.com/obris-dev/openmagpie/commit/d824015688d16876b27cec75fc448f6ab0e309b0))
* **cli:** treat missing id as failed persistence, not a '?' success ([4a6650b](https://github.com/obris-dev/openmagpie/commit/4a6650b429b5ecd6d81cb32bfb7173a2e014c544))
* **common:** release job locks on SIGTERM + add clear_job_locks ([#72](https://github.com/obris-dev/openmagpie/issues/72)) ([ac5fc56](https://github.com/obris-dev/openmagpie/commit/ac5fc56024e7b5704f05b6a19299c4dc4e1983d1))
* **core:** own the venv as the non-root user so dev sync works ([#59](https://github.com/obris-dev/openmagpie/issues/59)) ([0400a1c](https://github.com/obris-dev/openmagpie/commit/0400a1ce44afbb11a999467bb6e5274b3048c817))
* **feeds:** add missing migration for FeedItem.data help_text ([61663ca](https://github.com/obris-dev/openmagpie/commit/61663ca366068cb8270f14d08bb32e4693495a69))
* **feeds:** address post-merge review of the quickstart seed ([#69](https://github.com/obris-dev/openmagpie/issues/69)) ([#71](https://github.com/obris-dev/openmagpie/issues/71)) ([f929967](https://github.com/obris-dev/openmagpie/commit/f9299676fba37029f8c57b6b41e2c76ce2fd026d))
* **feeds:** dry-run create reports the would-be source count ([7de4f1f](https://github.com/obris-dev/openmagpie/commit/7de4f1f2d964160fb554b4952114677b2341579a))
* **feeds:** dry-run create reports the would-be source count ([3e1d7f0](https://github.com/obris-dev/openmagpie/commit/3e1d7f0dffe9b92b89fe3b0864d62eee9b99769f))
* **feeds:** poll sources in random order, streamed ([3dcba3c](https://github.com/obris-dev/openmagpie/commit/3dcba3c83141ad85955e08796515ef9b223cf74e))
* **feeds:** poll sources in random order, streamed ([8dded5a](https://github.com/obris-dev/openmagpie/commit/8dded5ad5bca5fd68f68b9a64266338604a4ffae))
* **listeners:** list endpoint robustness (review H1, H2) ([3673f89](https://github.com/obris-dev/openmagpie/commit/3673f89c12ab3fd5b83478524ed89f47b9a9eeb3))
* **listeners:** notifier secret round-trip — kind-filtered pairing + fail-loud ([ef8b8f3](https://github.com/obris-dev/openmagpie/commit/ef8b8f32638c134328a6ecda696d47c8957f890b))
* **listeners:** resolve code-review blockers (C1/C3/H3/C2) ([87aadc3](https://github.com/obris-dev/openmagpie/commit/87aadc3c1bc399dce1efc8a262645b035a2fad98))
* **listeners:** second-review fixes + response DTOs + template file ([71b991d](https://github.com/obris-dev/openmagpie/commit/71b991dcbd9de671fa7303747630c12c2e50e62c))
* **quickstart:** default the seed backfill to 1 day ([#90](https://github.com/obris-dev/openmagpie/issues/90)) ([0360ce5](https://github.com/obris-dev/openmagpie/commit/0360ce5b86fe39e13773851bb5cc640a83142492))
* **quickstart:** fresh-DB up --wait deadlock + one-command quickstart hardening ([#75](https://github.com/obris-dev/openmagpie/issues/75)) ([305b4a6](https://github.com/obris-dev/openmagpie/commit/305b4a644bc80c6e86f71db6b92c5ccf0886688a))
* **quickstart:** shellcheck source=/dev/null so pre-commit passes on single-file edits ([4dc37e7](https://github.com/obris-dev/openmagpie/commit/4dc37e730f07d0bff026788adf2831196f6a8c5e))
* **quickstart:** silent preflight probe for ghcr.io registry access ([#88](https://github.com/obris-dev/openmagpie/issues/88)) ([7dd3f74](https://github.com/obris-dev/openmagpie/commit/7dd3f74c4b8362504a186806bd8414544f647eb1))
* **quickstart:** skip Git LFS smudge on the bootstrap clone ([#96](https://github.com/obris-dev/openmagpie/issues/96)) ([25b4019](https://github.com/obris-dev/openmagpie/commit/25b4019dfefcb396adc33afa3ad52c6041933c5d))
* **quickstart:** tell the user which LLM situation they are in at the engine prompt ([#89](https://github.com/obris-dev/openmagpie/issues/89)) ([8093171](https://github.com/obris-dev/openmagpie/commit/8093171de5e6151ca936db54f076fd873eeff04f))
* **reddit:** re-indent page-handling block inside pagination loop ([9fe47c2](https://github.com/obris-dev/openmagpie/commit/9fe47c2fd95f57da5e7bffa78d9958a38458b875))
* **reddit:** re-indent page-handling block inside the pagination loop ([b2d4619](https://github.com/obris-dev/openmagpie/commit/b2d46193d075aed65207cfb4a1cadc2e097fd4e4))
* **reddit:** swap anonymous .json for .rss Atom endpoint ([913195a](https://github.com/obris-dev/openmagpie/commit/913195a3e43b2feb2d0e2096fd84e83fa9b3f25a))
* **reddit:** swap anonymous .json for .rss Atom endpoint ([9ded590](https://github.com/obris-dev/openmagpie/commit/9ded590c15b03d1c90466c6dc669ef6951f6d084))
* **rss,reddit:** drop the bozo half of the parse gate ; it over- ([92168c3](https://github.com/obris-dev/openmagpie/commit/92168c30dc6eb123e4d70fdd6290cc9eea5b4be8))
* **rss,reddit:** raise on 200-with-HTML rate-limit / block pages ([b444d1b](https://github.com/obris-dev/openmagpie/commit/b444d1bd782434caaf2e6d42fbd748eda3c3ab8e))
* **rss:** SSRF guard at source create + connector ; stream body ([25ce480](https://github.com/obris-dev/openmagpie/commit/25ce48063c7359d1f9d293cdbdbf1f6f739a4a8b))
* **rss:** unwrap Chromium XML-viewer body from challenge bypass ([d61a098](https://github.com/obris-dev/openmagpie/commit/d61a098a0770b991a4ee7ed3a122ceff900f0a69))
* **rss:** unwrap Chromium XML-viewer body from challenge bypass ([baf0085](https://github.com/obris-dev/openmagpie/commit/baf00858718c23d906de969771e07a9ac3298653))
* **sources:** back off Reddit 429s; fail sources honestly on connector errors ([#87](https://github.com/obris-dev/openmagpie/issues/87)) ([f8043dd](https://github.com/obris-dev/openmagpie/commit/f8043ddaff94b6ac3cf69a13298c947c5b3ea977))
* **sources:** flip watermark gate to strict `<` ; factor streamed- ([c9e1f47](https://github.com/obris-dev/openmagpie/commit/c9e1f472dbca9f42b7a297b41400bc8d8d00d822))

## [Unreleased] - 2026-06-09

### Added

- Personal access tokens for headless / no-browser CLI login. Mint one on the
  server with the `issue_cli_token` management command (on the local stack:
  `make local-manage CMD="issue_cli_token --email <e> --name <n>"`), then sign in
  with `magpie auth login --token` (reads the token from piped stdin or a hidden
  prompt, never argv; persisted to `~/.magpie` at `0600`). For CI, set
  `MAGPIE_TOKEN=mgp_...` in the environment instead: it's read as an ambient credential
  on every request (precedence over a stored login, never persisted), the `GH_TOKEN`
  pattern, no login step. Tokens are hashed at rest, named,
  and individually revocable via `magpie auth token list` / `create` / `revoke` or
  `DELETE /v1/auth/cli-tokens/<id>`; a token can't mint another token (browser
  login required to create). This unblocks running magpie on a box where the
  device-flow URL (the web app on `:3001`) is not reachable.
- One-command quickstart, replacing `make quickstart` / `make local-seed`.
  `curl -fsSL https://openmagpie.ai | sh` clones the repo and runs the make-free
  installer (`scripts/quickstart/run.sh`): it checks Docker, builds the stack,
  migrates, seeds an example feed + watch, and runs one pipeline pass, so a fresh
  machine reaches a real match in one command. In a clone, run
  `./scripts/quickstart/run.sh` directly; `STARTER` / `DAYS` choose a different
  starter or backfill window. Getting-started moved out of `make` into
  `scripts/quickstart/` (POSIX `sh`, Docker the only prerequisite); `make`
  remains the dev-loop interface.
- Webhook deliveries audit: every outbound webhook call is recorded as a
  `WatchActionDelivery` (one row per HTTP attempt) with its state, HTTP status,
  method, item count, redacted target host, and the exact request body sent (no
  headers). View it with `magpie delivery list --action <action_id>` or
  `GET /v1/actions/<action_id>/deliveries`. Each run links to the call that
  carried it via `delivery_id`.
- Webhook actions support an HTTP `method` of `POST`, `PUT`, or `PATCH`
  (default `POST`).

### Changed

- Relevance engine generalized to any OpenAI-compatible `/v1` endpoint (Ollama,
  vLLM, llama.cpp, LM Studio, OpenAI, or a hosted provider), driven by the
  official `openai` client. **Breaking config rename:** `OLLAMA_URL` /
  `OLLAMA_DEFAULT_MODEL` become `ENGINE_BASE_URL` / `ENGINE_MODEL` (plus an
  optional `ENGINE_API_KEY`). To upgrade an existing install, set
  `ENGINE_BASE_URL` (e.g. `http://host.docker.internal:11434/v1`) in
  `apps/core/.env`; a missing value now fails startup with a named
  `ImproperlyConfigured` naming the old and new vars, not a raw `KeyError`.
  `ENGINE_MODEL` is optional (unset still boots, with an `engine.W001` warning).
  A data migration rewrites any stored `semantic_filter` `engine.kind: "ollama"`
  to the server default.
- CLI command tree reshaped so the structure matches how data is used, not ORM
  containment. Observability is now flat and top-level: `magpie activity
  summary` / `list` / `get` and `magpie delivery list` / `get`, each scoped by
  `--action <id>`, REPLACE the buried `magpie watch action activity` /
  `deliveries` / `delivery`. The overloaded `activity` (summary AND run log) is
  split into `activity summary` vs `activity list`. A feed's source set moves
  under a `feed source` sub-noun (`list` / `set` / `export --feed`, `delete` /
  `get <source_id>`, `template`), replacing the `feed *-sources` verbs;
  `watch action list` / `add` take `--watch <id>` instead of a positional. A
  bare positional is now always the resource's own id; a scope is a named flag.
  `delete` is the single destructive verb across every noun (the child-only
  `remove` is gone; `feed source` / `watch action` use `delete` like `feed` /
  `watch`).
  Observability views render a human table by default and emit newline-delimited
  JSON with `--jsonl`. The run audit now shows each item's title + feed name
  (joined server-side) and, for `semantic_filter` actions, the filter score and
  reason.
- New read commands for reviewing definitions: `magpie watch action get
  <action_id>` shows one action's kind + config (it was only addable / settable /
  removable before). A feed's items move to a read-only `feed item` sub-noun
  (`list --feed <id>`, `get <item_id>`; no create / edit / delete), replacing
  `magpie feed view`: `feed get` now shows the feed's definition and `feed item
  list` its content stream, so the two no longer read as synonyms.
- The webhook payload is now one self-describing shape for both instant and
  digest delivery:
  `{watch: {id, name}, action_id, delivery, window, items: [{key, source: {label, kind}, item}]}`
  (instant is a one-item batch with `window` null). Each item now carries the
  source it came from. This REPLACES the previous instant `{action_id, item}`
  and digest `{action_id, items: [{key, item}]}` shapes; receivers must adopt
  the unified shape. Delivery is
  at-least-once; receivers dedup per item on the in-body `key`.
