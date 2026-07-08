# core/AGENTS.md

Conventions for the Django backend. Cross-cutting rules live in [../AGENTS.md](../AGENTS.md).

## Layout

```
core/
  common/          BaseModel (ULID PK + timestamps), ULIDField, locks, db ceilings, /healthz view
  accounts/        User (email login), Account, UserProfile + services/
  auth_api/        DRF auth surface: signup / login / logout / me + tokens/* + device-sessions/*
  sources/         Connectors (RedditSubRedditConnector, ...) + SourcePayload hierarchy + per-(source,kind) registry
  feeds/           Feed + Source + FeedItem models, poll orchestrator, item log
  engine/          Engine Protocol + OpenAICompatEngine + registry (+ probe)
  watches/         Watch + WatchFeed + WatchPath + WatchAction + WatchActionRun + WatchActionDigestWindow + WatchActionDelivery
  conf/            settings (base + local override), urls, wsgi
```

A **Watch** is a subscription over a set of feeds plus an ordered chain of
**actions** run against each new feed item (a filter is one action kind, a
delivery is another). The `watches/` sub-structure:

```
watches/
  models/                 one file per model (Watch, WatchFeed, WatchPath, WatchAction, WatchActionRun, WatchActionDigestWindow, WatchActionDelivery)
  services/
    watches/              WatchService (+ _actions.py chain ops, _global.py cross-tenant)
    runs/                 WatchActionRunService + Global (_service.py enqueue / claim / complete;
                          _drain.py cross-tenant drain; _backfill_select.py BackfillSelectMixin: backfill
                          source-select + terminal-delete over a `present` Subquery)
    backfills.py          WatchActionBackfillService (+ Global claim/reap) for the queued backfill jobs
    _run_batches.py       DigestBatchMixin (digest_batch / complete_batch / fail_batch)
    digest.py             WatchDigestWindowService (window open/close coordination) + Global
    deliveries.py         WatchActionDeliveryService (record / list) for the HTTP-call audit
  actions/                EXECUTION registry: protocol.py (ONE Action interface: run(items, context) -> ActionResult
                          for every kind, uniform dispatch no branching ; OutboundActionResult adds the `outbound`
                          OutboundCall record), registry.py (kind -> impl), the per-kind impls (semantic_filter.py /
                          extract.py / webhook.py / log.py) + shared mixins (_engine_action.py prepare, _fetch.py /
                          _external.py), _config.py (load_typed). Webhook emits one self-describing payload (watch + window +
                          per-item source), supports POST | PUT | PATCH, and returns an OutboundActionResult
                          so the operations layer logs a WatchActionDelivery per call.
  operations/             one-shot orchestrators: trigger.py, drain.py, digest_flush.py, advance.py (enqueue_next),
                          backfill.py (WatchBackfillOperation: re-run an action over the previous step's passes),
                          run_inputs.py (build_run_inputs: enrich runs+items+watch into ActionItem/ActionContext)
  registry.py             CONFIG registry: kind -> Pydantic config class (parse / validate / load_config)
  policy.py               write-time guards (engine registered, digest interval bound, webhook SSRF)
  run_messages.py         operator-facing WatchActionRun.error + backfill-job.error strings (sanitized; raw cause -> logs)
  management/commands/    process_due_watches (trigger) / process_due_backfills (queue setup) / process_due_runs (drain) / process_due_digests (flush)
  api.py / views.py (watch/action CRUD) / views_audit.py (runs + deliveries read views) / views_backfill.py (backfill submit + status/list)
  serializers.py / urls.py / action_urls.py (leaf /v1/actions/<id>) / constants.py
```

## File shape per app

```
<app>/
  services/<resource>.py    # CRUD + read services
  models/<model>.py         # Django models (no admin)
  apps.py                   # may have ready() to load registries
  migrations/
  tests.py                  # split into tests_<topic>.py when it nears the 350-line cap
  views.py                  # only if the app exposes HTTP
```

Apps are created with `python manage.py startapp <name>` inside the container (`make local-manage CMD="startapp <name>"`), then customized per these conventions.

## Models & data access

- **Every model inherits `common.models.BaseModel`** -> ULID primary key + `created_at` + `updated_at`.
- **Order by the ULID PK, never `created_at`.** ULIDs are lexicographically sortable by creation time (the timestamp is the high bits), so `order_by("-id")` is newest-first using the indexed primary key. `created_at` is a redundant sort key with worse properties (timestamp ties, clock skew, an extra index). Use `id` for chronological ordering; `created_at`/`updated_at` are for display/audit, not sorting.
- **No `ForeignKey`. Char pointers only.** Cross-model references are `CharField(max_length=26)` named `<thing>_id`. Stale data is OK; no cascades; no auto-indexes. Add `db_index=True` only when a specific lookup needs it. Because there is no cascade, a service that deletes a parent owns the cleanup of its children (e.g. `WatchService.delete` removes the watch's feeds, paths, actions, runs, AND digest-window rows in one transaction).
- **No direct `Model.objects.*` outside the model's owning service module.** All access goes through `<app>/services/<resource>.py`.
- **Services are classes, not loose module functions.** One class per primary entity (e.g. `WatchService`, `WatchActionService`, `FeedService`). Instance methods for account-scoped operations; a nested `class Global:` (with `@staticmethod` methods) for cross-tenant operations.
- **Account-scoped services bind their scope in `__init__`** and raise `ValueError` if `account_id` is missing or empty. Methods then drop the `account_id=` kwarg; `self.account_id` is the single source of truth. Scoped services also assert that incoming domain objects match `self.account_id` (defense-in-depth at the seam; `ValueError` if a foreign-account object is handed in).
- **System-level operations live under `<Service>.Global`** as static methods. These are the only place cross-tenant queries happen; reach for them sparingly (schedulers, admin / debug entry points). The drain's `WatchActionRunService.Global.claim_due` and `WatchDigestWindowService.Global.iter_due` are the cross-tenant scans the crons drive.
- **Every user belongs to an account.** Signup binds one (`SignupOperation`), and `AuthUser.account_id` is non-null, so `/v1/auth/me` + login raise on an account-less user. `User.objects.create_superuser` therefore REQUIRES an `account_id` (raises `ValueError` without one, and checks the account exists) and binds the admin to it. `manage.py createsuperuser` passes none and will raise mid-prompt, so mint an admin from a shell instead: create or pick an Account, then `User.objects.create_superuser(email=..., password=..., account_id="<id>")`.
- **One-shot orchestrators are `Operation` classes**, not `Service` classes. Build with the domain object, call `.run()` once, discard. Use this when an action has internal state across helpers (counters, watermarks) and would otherwise force every helper to thread the same args. Examples: `FeedPollOperation(feed).run()` (poll a feed's sources), `WatchTriggerOperation(watch).run()` (enqueue runs for new feed items), `WatchDrainOperation(run).run()` (execute one claimed run), `WatchDigestFlushOperation(window).run()` (emit one digest batch). The `Service` suffix stays reserved for reusable, account-scoped services; `Operation` signals "single-use, not reusable."
- **Operations instantiate scoped services internally** from the domain object's `account_id`; callers just hand in a domain object. Service constructions belong on `@cached_property` so `__init__` stays validation-only.
- **`get`/`get_by_<field>` raise `DoesNotExist`.** Never return `None`. Type stays `-> Model`; callers handle missing via `try/except`. If "might not exist" is the normal path, add a separate `find_by_<field>` returning `Model | None`.
- **Return iterators for collections.** Use `.iterator(chunk_size=N)`; callers `list(...)` if they need to materialize. Bulk writes use `.bulk_create()` / `.bulk_update()`.
- **Chunk `id__in` / bulk lists under the DB parameter ceiling.** Backends cap host parameters per statement (`common.db.ID_IN_CHUNK`); chunk with `itertools.batched(ids, ID_IN_CHUNK, strict=False)`. This bounds the per-statement width, not peak memory; bound memory separately (e.g. `DIGEST_MAX_BATCH_ITEMS` caps a digest batch before it's loaded).
- **Every query hits an index.** A service WHERE is covered two ways. **Fully** — every column is indexed via `db_index=True`, a `UniqueConstraint`, or `Meta.indexes`; don't add an explicit index when a `UniqueConstraint` already left-prefix-covers the read path. **Partially** — an indexed *prefix* carries the selectivity, narrowing the read to one bounded scope, and the remaining column(s) are filtered on that bounded result with no index of their own: the filter's cost scales with the *scope's* row count, not the table's (e.g. `kind` atop the `(account_id, feed_id)` prefix sifts a single feed's sources in `iter_by_kind` / `iter_for_poll(exclude_kinds=…)`, never the whole Source table). Partial coverage holds ONLY when the prefix is what makes the query selective — a trailing filter the prefix does NOT first narrow to a bounded scope is a table scan and needs its own index. Correlate cross-table subqueries on the full key so they ride an existing index (e.g. `claim_due`'s `~Exists` over the digest-window table correlates on `(account_id, action_id)`, the window's unique key).

### Call-site shape

```python
# Account-scoped (the common case)
svc = WatchService(account_id=account_id)
watch = svc.get(id)
actions = svc.action_svc.list_for_path(watch.initial_path_id)

# Cross-tenant (rare; scheduler, admin)
for watch in WatchService.Global.iter_active():
    ...
for feed in FeedService.Global.iter_due_for_poll(now=now):
    ...
for run in WatchActionRunService.Global.claim_due(now=now):
    ...

# One-shot Operations (the crons)
WatchTriggerOperation(watch).run()                # enqueue runs for new items (computes now internally)
WatchDrainOperation(run, now=now).run()           # execute one claimed run
WatchDigestFlushOperation(window, now=now).run()  # emit one digest batch
```

## Scoping

- **Every domain model carries `account_id` + `user_id`.** `User` and `Account` themselves are exempt; they *are* those entities. **Auth credentials are also exempt from `account_id`**: `auth_api.CliToken` (the CLI personal access token) carries only `user_id`, because it authenticates a *user*, not an account, the active account is resolved per request from the user's primary account, exactly like the OAuth session token. Pinning an account into the credential would diverge from how sessions behave.
- **Every account-scoped service query filters by `self.account_id`.** Cross-tenant data leakage is impossible by construction. The only escape hatch is `<Service>.Global.*` for explicit system-level ops.
- **Unique constraints are `account_id`-first** so an account-scoped read rides the constraint's backing index as a left-prefix. As shipped: `(account_id, watch_id, feed_id)` on WatchFeed; `(account_id, path_id, rank)` on WatchAction; `(account_id, watch_id, action_id, feed_item_id)` on WatchActionRun; `(account_id, action_id)` on WatchActionDigestWindow. The `(state, scheduled_at)` drain index on WatchActionRun is deliberately account-agnostic (the drain is a global scan).

## Types

- All service functions, manager methods, helpers: fully type-annotated.
- `django-stubs` is installed so ty resolves `.objects`, manager generics, and field descriptors. When something still trips ty, fix it properly: explicit `ClassVar[Manager[Self]]` annotation, `cast()` at the field boundary, or a small helper in `common/`. `# type: ignore` is a last resort with the specific rule name, used only when no principled fix exists.
- Generic helpers use PEP 695 type parameters (`def load_typed[T: WatchActionConfigBase](...)`), not `TypeVar`.
- Run `make local-types` before declaring done. Don't reach for `# type: ignore`, `# noqa`, or workarounds to make checks pass; find the root cause.

## Typed-blob pattern (Feed, WatchAction, WatchActionRun)

Each model carries queryable common fields top-level + a `data`/`config`/`result` `JSONField` whose schema is owned by a Pydantic class (registered per `kind`). The shared classes live in `packages/openmagpie-schema` so the server and CLI validate against one definition.

**Convention for those Pydantic classes: a mutable field default is `Field(default_factory=...)`, never a bare `[]` / `{}` / `Model()`.** Pydantic v2 deep-copies bare defaults so they are not a shared-mutable-state bug, but the package is uniform on `default_factory` (see `watch.py`, `feed.py`); keep new fields consistent. Immutable defaults (`0`, `""`, `None`, a `Literal`) stay inline.

- **`Feed.data`** is validated by a Pydantic config keyed off `Feed.kind` (see `feeds.registry`). v1 kind is `"curated"` -> `CuratedFeedConfig` (retention + default_field_map). The actual source set lives on `feeds.Source` rows; each row owns its own watermark. The Feed owns the poll loop.
- **`WatchAction.config`** is the PURE kind-specific blob; the discriminator `kind` is a sibling column, NOT nested in the blob (k8s-style adjacent tag). Validated by `watches.registry` (kind -> config class): `semantic_filter` -> `SemanticFilterConfig`, `webhook` -> `WebhookConfig`, `log` -> `LogConfig`. Per-kind classes carry a `CONFIG_KIND` ClassVar and no `kind` field. Validation is a registry dict, NOT a Pydantic discriminated union.
- **`WatchActionRun.result`** is the kind-specific result blob (validated per kind), stored for the run audit: `SemanticFilterResult {passed, score, reason}`, `WebhookResult {http_status}`, `LogResult {rendered}`.
- **`FeedItem.data`** is the full `SourcePayload.model_dump()` of a polled item (the browsable log; all items). `FeedItem.source_kind` / `source_label` / `source_meta` denormalize the producing Source row for cheap read paths. Actions read live `FeedItem.data` at run time (the run row stores no item snapshot).
- **Secret-bearing configs** (webhook url + header values) implement `redacted_dump()` (mask on read) and `merge_preserving()` (restore an unchanged masked secret from the prior row on edit). The server never returns a real secret; an edit that submits a still-masked secret with no same-kind prior to restore from is rejected.

## Watch execution model: trigger -> drain -> flush

A `WatchActionRun` is ONE action against ONE FeedItem. Four cron stages, each a `SingleFlightCommand` (a pass that outruns its interval self-skips instead of stacking), each backed by `common.locks.job_lock`. Stage 1b (backfill) is on-demand (only does work when backfill jobs are queued):

```
# stage 1 — TRIGGER (process_due_watches): enqueue head-action runs for new items
for watch in WatchService.Global.iter_active():
    head = initial_actions(watch)[0]            # first action by rank order, NOT a rank==0 lookup
    for feed in subscribed feeds:
        scan FeedItem.id > WatchFeed.last_item_id
        if head is a digest: open its window first, stamp scheduled_at = window close
        enqueue a PENDING run for `head` per item   # idempotent on (account, watch, action, feed_item)
        advance WatchFeed.last_item_id

# stage 1b — BACKFILL (process_due_backfills): on-demand re-enqueue over history
reap_stale()                                   # RUNNING past WATCH_BACKFILL_STALE_SECONDS -> FAILED (retryable/terminal by completed_at)
for job in claim_due():                         # CAS PENDING/retryable-FAILED -> RUNNING, attempts += 1
    WatchBackfillOperation(job).run()           # resolve source; if replace, delete terminal runs of target + downstream; enqueue PENDING target runs
# the DRAIN below then executes those runs. Ordered before DRAIN only where the tick
# is serial (local-tick / tick.sh); under independent tickers they drain next pass.

# stage 2 — DRAIN (process_due_runs): execute due per-item runs
reap_stale()                                   # RUNNING past WATCH_RUN_STALE_SECONDS -> FAILED (crashed worker)
for run in claim_due():                         # CAS PENDING/FAILED -> RUNNING, attempts += 1; excludes digest actions
    outcome = registry.get(action.kind).run(action, item_data=item.data)
    complete(run, outcome)                      # guarded CAS write
    if outcome.state == SUCCEEDED: enqueue_next(run, action)   # advance to next rank (instant: now; digest: window close)

# stage 3 — FLUSH (process_due_digests): emit accumulated digest windows
for window in WatchDigestWindowService.Global.iter_due():
    WatchDigestFlushOperation(window).run()     # gather the action's pending runs (capped), run_batch, complete, advance, close-if-drained
```

- **"Due" = `state == PENDING AND scheduled_at <= now`** (the `(state, scheduled_at)` index). Instant delivery stamps `scheduled_at = now`; a digest successor is stamped the window close. Flushing is clock-driven, never an item arrival.
- **The row IS the lock at both ends.** `claim_due` and `complete` are compare-and-swap UPDATEs keyed on `(state, attempts)`: overlapping drains can't double-execute, and a drain whose claim was reaped + re-taken mid-run can't double-complete (its stale write matches no row, returns None, never advances the chain). `attempts < WATCH_RUN_MAX_ATTEMPTS` bounds retries; the reaper recovers crashed RUNNING rows.
- **Failure taxonomy.** `SUCCEEDED` -> advance the chain. `GATED` -> ran cleanly but halts the chain (a semantic filter scoring below threshold; not a failure). `ERRORED` -> permanent defect the impl detected (deleted action / pruned item / unknown kind / blocked webhook / 3xx-4xx) -> no retry. `FAILED` -> transient (5xx / timeout); retried while under the attempts cap, then terminal. `SKIPPED` -> deliberate non-run. Impls return SUCCEEDED / GATED / ERRORED; they raise only on UNEXPECTED failure, which the drain maps to retryable FAILED. `run.error` is a sanitized `run_messages` string; the raw cause goes to the logs keyed by run id.
- **The expensive leg runs OUTSIDE any transaction** (the LLM judge, the webhook POST). Only the terminal write + the next-action enqueue share one short atomic block, so a SUCCEEDED run and its successor commit together without holding a lock across the network call.

## Action chain rules

- **Actions belong to a `WatchPath`, ordered by a dense integer `rank`** (0..N-1 today, unique `(account_id, path_id, rank)`). Resolve chain position by ORDER, never by a literal rank value: the chain entry is the lowest-rank action (`list_for_path(...)[0]`, rank-ordered), and "next" is the smallest rank strictly greater (`next_in_chain`) — both gap-safe so a future sparse-rank optimization can't break them. Don't write `WHERE rank == 0` / `rank + 1`. v1 creates exactly one path per watch; `Watch.initial_path_id` points at it. A watch subscribes to a SET of feeds (`WatchFeed` rows, each with its own `last_item_id` watermark).
- **The chain-shape mutators take `path_chain_lock(path_id)`**: `add`, `remove`, and `replace_chain` (they renumber ranks). `set_config` does NOT lock — it edits one row in place without touching ranks, so there is no chain-shape race to guard. HARD RULE: acquire the lock OUTSIDE the transaction (`with lock: with atomic:`); a lock released before commit corrupts. So `WatchService.create`/`update` run `replace_chain` OUTSIDE their scalar+feed transaction (an edit is two scopes). Loser of the lock -> `ConcurrentChainError` -> 409.
- **`replace_chain` is an upsert by action id** (Terraform `for_each`, not `count`): a spec with a known `id` updates that row in place (id + run history + secret survive), no id mints a new action, an absent row is deleted, ranks renumber densely. This is what makes a reorder safe and keeps the secret with its own endpoint.
- **A digest delivery is allowed at ANY position, including the chain head.** Whoever enqueues the digest action's runs opens its window first (so `claim_due` excludes them and they batch instead of delivering instant): for a head digest that's the **trigger** (it has no preceding action to open the window on advance, so it opens it once per cycle before the head runs land); for a non-head digest that's the **advance** (`enqueue_next` opens the window when the preceding action succeeds). There is no positional guard.
- **Moving an action off digest (or removing it) drops its `WatchActionDigestWindow` row.** `claim_due` excludes a run while its action has a window row, so a lingering window strands the now-instant runs PENDING forever. An edit INTO digest leaves window opening to the trigger/advance on the next item; `set_config` only deletes a window (on a digest->instant edit), never creates one.

## Delivery: instant vs digest

- **`delivery` is a config field on the delivery kinds** (webhook, log), shared via `DeliveryConfigBase` (`delivery: instant|digest` + `digest_interval_seconds`). `semantic_filter` is a filter, not a delivery, so it doesn't carry these. The presence rule (DIGEST requires a positive interval) is a pure invariant in the shared schema; the magnitude bound (min/max seconds) is settings-coupled and lives in `policy`.
- **A digest is the ACTION's property, not the run's** — there is no per-run digest flag. A run is "digest" iff its action has a `WatchActionDigestWindow` row. The window is a FIXED window anchored at first arrival (`close_at = now + interval`; later arrivals join without extending it). Window open/close is coordinated by `select_for_update` on the window row INSIDE the caller's transaction (a cache lock can't live in the drain's completion txn).
- **The flush gathers the action's pending runs as the batch** (capped at `DIGEST_MAX_BATCH_ITEMS`; a larger window drains over successive flushes), emits once via the impl's `run` (webhook: one POST with per-item `key`s for receiver dedup; log: one entry), marks them succeeded + advances each, then closes the window iff drained (re-checked under the row lock so a straggler during emit isn't orphaned). A transient batch failure burns one attempt per run (the digest analog of the claim-time cap) so a down destination drains to terminal instead of re-emitting forever.
- **Digest delivery is at-least-once by design**: the emit is OUTSIDE the terminal transaction, so a crash after a successful emit but before the commit re-emits next flush. Webhook carries a per-item idempotency `key`; a duplicate log line is harmless. Receivers MUST dedup on the key.

## Plugins (connectors, engines, actions)

Same shape inside each owning app:
```
app/
  <thing>s/
    protocol.py / base.py   # Protocol + shared DTOs
    <impl>.py               # concrete plugins
  registry.py               # name -> instance
```

- Adding a new plugin = one file + one registry entry.
- Connector classes declare both `kind: str` and `payloads: list[type[SourcePayload]]`; the `register(...)` call references the class attrs (no string duplication).
- App `ready()` hooks import the registry so plugins self-register at Django startup, not lazily.
- **Action kinds have TWO registries, kept separate**: the CONFIG registry (`watches.registry`, kind -> Pydantic config class, validation) and the EXECUTION registry (`watches.actions.registry`, kind -> runnable `Action` impl). An action impl declares `kind` and implements `run(action, *, item_data)`; delivery kinds also implement `BatchAction.run_batch(action, *, items)` for digests.

## HTTP API

- `djangorestframework` is in. All endpoints are DRF `APIView` CBVs (CBV + serializer for input + serializer for output).
- Keep `API_VERSION_PREFIX` in `core/conf/settings/base.py` in lockstep with `NEXT_PUBLIC_API_VERSION` in the web app.
- **Every `/v1/` route is trailing-slash-optional.** Use the two helpers in `common/urls.py` instead of `path(..., include(...))` / `path(..., view)`:
  - **`api_include(prefix, module)`** for every mount of an app's urlconf into a parent.
  - **`api_path(route, view, name=...)`** for every leaf route inside a urlconf.
  Django's `APPEND_SLASH` only fixes GETs (POST/PUT don't follow the 301), so matching both forms in URL resolution is the proper fix.

### Discriminated-config endpoints

For resources whose schema varies by `kind` (a `WatchAction` with `kind=semantic_filter|webhook|log`), the write accepts an envelope `{kind, config}` with `kind` a sibling of the config blob. Validate `config` via the Pydantic registry that owns the typed-blob schema (`watches.registry.validate_config(kind, data)`); don't duplicate the schema in a DRF serializer. Translate Pydantic `ValidationError` into DRF's nested 400 shape so a deep failure surfaces at the right path; a bad kind keys at `actions.N.kind`. Example: `watches/serializers.py`.

### Normalized list responses (keyed side tables, not per-row embeds)

When list rows reference a related entity, **don't embed that entity on every row.** Put the rows' foreign keys on the rows and return the referenced entities once in a keyed side table on the envelope (`{id -> wire}`); the client joins by id in memory. Use one map per related type, and keep the side-table value models lean (display fields only). A referenced id with no entry (pruned / cross-account) is absent from its map; the row still renders by its id. Batch-resolve each map with a service `get_many` (no N+1). Don't reach into opaque JSON blobs (`result`, `FeedItem.data`) to build query filters; project display fields out of them in the serializer instead. Example: `ActionRunsView` returns `items` (run rows carrying `feed_item_id`) plus `feed_items {feed_item_id -> ...}` and `feeds {feed_id -> ...}`.

### Route naming

- A first-class entity (a hub other resources are addressed relative to) is a bare collection: `/v1/feeds`, `/v1/watches`, `/v1/actions`. Address one by its own globally-unique ULID at the flat collection (`/v1/actions/<id>`), even when it is created under a parent (`POST /v1/watches/<id>/actions`).
- A dependent record/component (no value apart from its parent) is parent-qualified, kebab-case, for its by-own-id detail route: `/v1/feed-items/<id>`, `/v1/feed-sources/<id>`, `/v1/action-activity/<id>`, `/v1/action-deliveries/<id>`.
- A child LIST stays nested under the parent; the sub-collection segment is bare, since the parent id is already in the path: `/v1/feeds/<id>/items`, `/v1/actions/<id>/activity`.
- Multi-word path segments are kebab-case (`feed-items`, never `feed_items` or `feeditems`).

### URL surface

```
/v1/auth/...                                       (see Auth section)

/v1/feeds                                          GET/POST   feeds in account
/v1/feeds/<id>                                     GET/PUT/DELETE
/v1/feeds/<id>/sources                             GET/PUT    source set (list / replace)
/v1/feeds/<id>/items                               GET        item log (cursor)
/v1/feed-sources/<id>                              GET/DELETE one source by own id
/v1/feed-items/<id>                                GET        one item by own id

/v1/watches                                        GET/POST   watches in account
/v1/watches/<id>                                   GET/PUT/DELETE
/v1/watches/<id>/actions                           POST       add an action (rank insert or append)
/v1/actions/<id>                                   GET/PUT/DELETE  one action by own id
/v1/actions/<id>/activity                          GET        run audit log ("activity", cursor, ?state=)
/v1/actions/<id>/deliveries                        GET        delivery audit log (cursor, ?state=)
/v1/actions/<id>/backfill                          POST       queue a backfill of this action (?dry_run= for a preview)
/v1/action-activity/<id>                           GET        one run by own id
/v1/action-deliveries/<id>                         GET        one delivery by own id
/v1/action-backfills[/<id>]                        GET        backfill jobs (list, cursor) + one job by own id

/v1/engines                                        GET        registered engines + reachability
/healthz                                           GET        DB + cache pings + product version (public)
```

## Cache-backed state pattern

When persisting structured state in `django.core.cache`:

- **Define a Pydantic model for the bag.** Never write raw `dict[str, Any]` directly into the cache.
- **Encapsulate I/O in a `Store` class** with `get` / `put` / `delete` statics. `Store` picks the TTL off the state's phase so callers don't pick TTLs by hand.
- **Views own the HTTP surface and auth checks. Store owns shape + I/O.** Views never reach into `cache` directly.
- Lifecycle transitions return a new state via a method on the model (e.g. `state.complete_with(...)`), preserving carried-over fields. The view writes the result back via `Store.put`.
- Example pairing: `core/auth_api/device_session_store.py` (state + Store) plus `core/auth_api/device_sessions.py` (views).

## Locks & scheduling

- **`common.locks.named_lock`** is the cache-backed try-lock primitive; it yields a `LockLease` (truthy iff acquired). A lock's `timeout` is a LIVENESS lease, not a total-work budget: a holder expecting to run long calls `lease.renew()` as it makes progress to re-stamp the TTL, so the lock survives a long-but-live run while a crashed holder frees after one window (etcd/k8s-Lease pattern). No fencing tokens — on these paths a brief overlap only costs redundant idempotent work, never corruption.
- **`common.locks.poll_lock`** uses that: `poll_feed` renews per source, so a feed of ANY size polls under one held lock (`POLL_LOCK_TIMEOUT_SECONDS` is the per-source liveness window). The poll loop stops early if a renew reports the lease was lost (another worker took over); per-source watermarks make that non-destructive.
- **`common.locks.job_lock`** (app-qualified key `<app>.<command>`, day-long TTL as a crash failsafe) backs `common.commands.SingleFlightCommand`: a scheduled command self-skips (logs) when a prior pass is still running, so plain cron is penalty-free.
- **`common.locks.path_chain_lock` / `feed_set_lock`** serialize chain / source-set mutations on one entity. Acquire OUTSIDE the transaction.
- **`select_for_update`** (not a cache lock) is used where the lock must compose with the caller's transaction (the digest window row).
- `make up-jobs` / `down-jobs` run the background tickers (poll, trigger, drain, flush) with pid+log under `.jobs/` (gitignored). `make local-tick` runs one pass of each stage now.

## Auth + identity

### Tokens

- **Issued by `django-oauth-toolkit`.** We don't drive its grant flows. `auth_api.services.tokens.mint_token_pair_for_user(user)` is the one seam that creates an `AccessToken` + `RefreshToken` pair against the singleton `magpie-cli` `Application` (public client). Revocation goes through `revoke_access_token(token)` in the same module; deletes the access row + revokes its paired refresh.
- **OAuth Application bootstrap**: `manage.py bootstrap_oauth_app` is idempotent and runs as part of `make local-migrate`. Creates the `magpie-cli` Application; the client_id is irrelevant to our flow.

### One token model, two delivery mechanisms

- **Browser** holds the access-token value in an HttpOnly `auth_token` cookie set by `auth_api.cookies.set_auth_cookie`. We do NOT use Django's session middleware for auth; the cookie literally carries the OAuth `AccessToken.token` value.
- **CLI** holds the same kind of value in `~/.magpie/config.json` and sends it as `Authorization: Bearer <token>`.
- **Lookup** is unified: `auth_api.authentication.BearerOrCookieAuthentication` checks the Bearer header first, then the `auth_token` cookie. Registered globally via `REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES`.
- **Personal access tokens** (`auth_api.CliToken`, the headless CLI login) are a third credential, a long-lived `mgp_...` bearer, hashed at rest. `PersonalAccessTokenAuthentication` is registered *ahead* of `BearerOrCookieAuthentication`: it owns the `mgp_` prefix and returns `None` for everything else, so non-PAT bearers + cookies fall through. A PAT-authed request carries `request.auth == CLI_TOKEN_AUTH`, which views use to forbid PAT-only-disallowed actions (minting more tokens).

### Auth URL surface

```
/v1/auth/signup                          POST   browser  signup + cookie
/v1/auth/login                           POST   browser  login + cookie
/v1/auth/logout                          POST   browser  clear cookie + revoke its token
/v1/auth/me                              GET    either   {user} (IsAuthenticated)

/v1/auth/tokens/refresh                  POST   CLI      rotate bearer pair
/v1/auth/tokens/revoke                   POST   CLI      bearer "logout"

/v1/auth/cli-tokens                      GET    either   list the user's personal access tokens
/v1/auth/cli-tokens                      POST   session  mint a PAT (rejects PAT auth: no PAT->PAT)
/v1/auth/cli-tokens/{id}                 DELETE either   revoke one PAT (owner-scoped)

/v1/auth/device-sessions                 POST   CLI      start handshake
/v1/auth/device-sessions/{id}            GET    CLI      poll (header: X-Device-Secret)
/v1/auth/device-sessions/{id}/info       GET    browser  audit metadata (IsAuthenticated)
/v1/auth/device-sessions/{id}/deny       POST   browser  decline (IsAuthenticated)
/v1/auth/device-sessions/{id}/complete   POST   browser  authorize (IsAuthenticated)
```

### Permission gating principle

- `permission_classes = [IsAuthenticated]` when the view needs an **identified user** (`/me`, `/device-sessions/{id}/info`, `/deny`, `/complete`).
- Open (`permission_classes = []`) when the endpoint consumes whatever credential is presented (signup, login, refresh, logout, revoke, device-session create/poll, healthz).
- Logout/revoke are intentionally open: gating breaks cleanup of stale credentials, which is exactly when callers need them most.

### CSRF defense for cookie auth

`BearerOrCookieAuthentication.authenticate` enforces an Origin-check on cookie-auth non-safe methods (anything other than GET/HEAD/OPTIONS/TRACE). Bearer requests are exempt. The allowed Origin list comes from `CORS_ALLOWED_ORIGINS` / `CSRF_TRUSTED_ORIGINS`. Per-view `permission_classes` do not need to re-check this.

### Device-flow handshake

`auth_api/device_sessions.py` is cache-backed via the cache-state pattern above. Pending TTL is 15 min; completed TTL is 5 min so leftover tokens don't linger after the CLI picks them up. Three secrets with deliberately separate roles (RFC 8628):

- **`session_id`**, public, in URL. Identifies the session; leaking it alone gives nothing.
- **`device_secret`**, CLI-only bearer for polling. Returned ONCE at create, stored as SHA-256 server-side. Required header on every poll.
- **`user_code`**, short human-typed code. Gates `POST /complete` so a phished browser can't authorize an attacker's CLI without seeing the code the attacker's terminal shows.

### Cross-app access

`auth_api` consumes accounts data through services only (`accounts.services.{UserService,AccountService,UserProfileService}.Global.*`). The signup multi-step (User + Account + UserProfile in one transaction) lives in `auth_api/operations/signup.py` as `SignupOperation`.
