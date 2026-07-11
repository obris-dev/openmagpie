# Plugins: extending OpenMagpie without editing core

This app is the extension system. It lets you register new plugin *kinds* (a
watch action, a connector, and, as categories are added, datastores, etc.)
**without editing a core registry file**. That keeps a fork cleanly mergeable
with upstream, and lets a third party ship an installable plugin. Everything a
plugin needs is discovered at startup; nothing in `apps/core` has to change.

## The pieces

- `registry.py`: `Registry[T]`, the generic `kind -> value` map (`register` /
  `get` / `known` / `kinds`). Use it for **new** categories; the four existing
  registries (`watches.actions`, `watches.registry`, `sources`, `engine`) keep
  their own.
- `loader.py`: `load_hooks()`, run once at startup by `apps.py`, discovers and
  invokes your **hooks**.
- `guards.py`: pure, testable helpers for the fail-loud settings guards.
- `db/routing.py` / `db/routers.py`: route a plugin app's models to a separate
  database.
- `db/config.py`: loads extra databases + routing from a JSON file.

## Registering a plugin (a "hook")

A **hook** is a zero-arg `register()` callable that registers your kind(s) into
the appropriate registry. There are three ways the app finds hooks at startup;
pick by how the plugin ships:

1. **Bundled** (core): a static import, as the built-in kinds do today.
2. **Env-pointed, no packaging** (the usual path for a fork). Keep a module in
   your fork's own package, an importable path *beside* `apps/core` (on
   `PYTHONPATH`), not a file under core, so core stays byte-identical to
   upstream. `myfork` below is a placeholder for your package name:
   ```python
   # myfork/plugins.py  (in YOUR fork's package, not apps/core)
   from plugins.registry import Registry

   my_things = Registry[MyThing]("my_things")   # a new category you own

   def register() -> None:
       my_things.register("acme", AcmeThing())
   ```
   ```bash
   OPENMAGPIE_PLUGIN_HOOKS=myfork.plugins:register   # comma-separated for several
   ```
   No `pip install`, no rebuild; edit the module and restart.
3. **Entry point, for distribution.** A `pip`-installed package advertises the
   hook, so another install picks it up with no source change:
   ```toml
   [project.entry-points."openmagpie.plugins"]
   my_plugin = "my_pkg.plugin:register"
   ```
   `OPENMAGPIE_PLUGIN_ALLOW` (comma-separated entry-point names) restricts which
   installed plugins load. When it is unset (or empty), the `local` (development)
   env loads every installed plugin and the `cloud` env loads none, because
   loading an entry point runs arbitrary code at boot and the hosted default
   fails safe. Note there is no separate "self-host production" settings module:
   any production deploy runs `DJANGO_ENV=cloud`, so a self-hosted **production**
   instance also defaults to none and must name the plugins it wants in
   `OPENMAGPIE_PLUGIN_ALLOW`. Only `local` loads all by default. An empty value
   is treated as unset (falls through to the env default) so a stray
   `OPENMAGPIE_PLUGIN_ALLOW=""` doesn't silently disable everything; set it to a
   non-empty list to opt plugins in. On `local` there is no allowlist value that
   means "load none" (empty falls through to load-all), so to disable an
   installed plugin in dev, uninstall it. An allowlist name that matches no
   installed plugin logs a **warning** at startup (almost always a typo); an
   installed plugin merely excluded by the allowlist is noted at **INFO**
   (routine on a locked-down deploy where everything is excluded).

A hook that fails to import or raises is logged and skipped, so one broken
plugin never stops the app from booting.

## Schemas across the boundary

There are two boundaries, kept consistent in different ways.

**In-process (core engine <-> plugin): reuse, not duplication.** A plugin runs in
the same process as core, so the contract is Python types, and a plugin
satisfies core's *existing* types rather than declaring parallel ones. An action,
for example, has two:
- **Execution:** the `Action` Protocol (`watches/actions/protocol.py`), a `kind`
  and `run(...) -> ActionResult`. It's structural, so `ty` checks a plugin's impl
  against it at type-check time.
- **Config/validation:** a Pydantic `WatchActionConfigBase` subclass, validated
  through the single `watches.registry.parse_config` / `validate_config`
  chokepoint. Reusing that base and path means there is no second schema to drift.

**Cross-service (server <-> CLI <-> web): one generated contract.** The backend's
Pydantic models in `openmagpie-schema` are the source of truth;
`tools/schema_sync` generates `packages/openmagpie-schema/schema.json` (and the
zod / TS types the web app and CLI consume), so all three stay in sync. That
generator builds from an **explicit list** of *core* models (`CONTRACT_MODELS`),
so a fork's models are not in the published contract. Server-side validation of a
plugin is always correct (it's driven by the Pydantic class at runtime); to give
the web app / CLI typed knowledge of a plugin's shapes, the fork runs the same
`schema_sync` pipeline over its own models and ships that extended contract.

**What's pluggable today.** A **new category** you define with `Registry[T]` is
fully drop-in. Two existing categories are ALSO extensible with a typed kind: the
**watch-action** kinds (`watch/_nodes.py` + `_runs.py` unions, `watches.registry`)
and the **source/connector** kinds (`configs.py` `SourceSpec`). Each union carries
a generic plugin fallback member so the server can build/emit any registered kind
and the CLI can parse it; the config registries expose `register` / `known_kinds`
(actions) or already read the registry live (sources). See the recipes below.
Adding a kind to the `engine` category is not wired (the engine is a single
env-driven backend); use `Registry[T]` for genuinely new categories.

### Recipe: a plugin watch-action kind
One call in a hook (`OPENMAGPIE_PLUGIN_HOOKS`) registers both halves; the drain then
routes to your impl automatically:
```python
# myfork/plugins.py  (in YOUR fork's package)
from watches.actions.registry import register_action

def register() -> None:
    register_action(MyMatchAction(), MyMatchConfig, result=MyMatchResult)
```
`register_action` registers the impl (execution routing) AND the typed config
(validation + the write-gate), and rejects a pair whose kinds disagree. `MyMatchConfig`
subclasses `WatchActionConfigBase` (declare `CONFIG_KIND`, implement
`redacted_dump`/`summary`/`merge_preserving`); `MyMatchAction` implements the `Action`
protocol with a matching `kind`. The stored `kind` is a bare column (max 32 chars, no
migration) and must be distinct from a built-in kind: registering one that collides
with a core kind raises rather than silently reshaping the built-in. (The two
underlying registries, `watches.registry` for config and `watches.actions.registry`
for the impl, are an internal split you don't need to touch.)

The optional `result=` (a Pydantic model) makes the run result ENFORCED: both terminal
paths (the instant drain and the digest flush) validate a SUCCEEDED run's result against
it and mark the run ERRORED on a mismatch, so a consumer (e.g. your web client) can rely
on the result shape the same way it relies on the config. Omit it to leave the result an
unchecked blob (the action's own responsibility, as with the built-in kinds). Note: on
the digest path one violating result is BATCH-fatal (the whole slice is marked ERRORED,
not just the offending item), since a digest run resolves a batch under one result.

### Recipe: a plugin source kind
```python
from sources.registry import register_source

def register() -> None:
    register_source(MyConnector())   # registers the connector + its payloads
```
`register_source` registers the connector (write-gate + poll routing) AND its
`payloads`. Your source spec is any Pydantic model with a `kind` field that isn't a
built-in kind; it validates through the `SourceSpec` fallback member (give it a
`display()`).

**SSRF:** core's write-time gate (`feeds/policy.py`) rejects a spec URL whose host is a
blocked IP literal, so a fork spec gets that defense-in-depth for free. The scan scopes
by a `URL_FIELDS: ClassVar[tuple[str, ...]]` on the spec: declare the fields your
connector fetches (e.g. `("url",)`) and only those are checked; declare `()` if the
spec has no fetched URL. A spec that DOESN'T declare it (including the open plugin blob)
is scanned in full, fail-safe. So an operator-supplied URL is always covered, and you
declare `URL_FIELDS` only to spare a display-only field a false rejection (the
tradeoff, for the full-scan open blob, is that a private-IP-looking string in an
UNRELATED field is rejected too; your fork owns its blob shape). Each name in
`URL_FIELDS` must be a REAL, SCALAR string field on the spec: a name that isn't a
field raises at the create seam (a hard 400/500, not a silent skip, so a typo fails
loud, not open), and a list/dict-valued field `str(...)`s to a non-URL and is skipped
(put a multi-URL spec behind the open plugin blob, which walks every leaf, instead of
naming a collection field). It's write-time and best-effort only: a plain hostname
passes there, so a fork connector that fetches an operator-supplied URL MUST still run
its own poll-time SSRF gate (re-resolve + re-check on redirect, ideally pinned-IP to
avoid DNS-rebinding). Core can't do the poll-time part for you.

### Typed web/CLI (federation)
The generic fallback carries `config`/`spec` as an untyped blob, so a plugin kind
is *usable* but not *typed* in the published contract. To give your kind typed
shapes in your fork's web/CLI, regenerate your OWN contract with the reusable
generator over your model lists (`tools.schema_sync.generate.render(...)` /
`write_or_check(...)`), and point the web generator at it via `OPENMAGPIE_SCHEMA_JSON`
/ `OPENMAGPIE_SCHEMA_TS`. Core's `schema.json` / `generated.ts` stay byte-identical.

Opening the unions also widens the inferred `kind` type in the generated web zod. The
built-in members keep their `z.literal(...)`, but the added plugin fallback member's
`kind` is `z.string()`, so the UNION's inferred `.kind` is `string`: a TS consumer that
does `switch (action.kind)` loses exhaustiveness over the built-in literals (no core web
code narrows on `.kind` today). Regenerating your own typed contract (below) adds your
kinds as literals; a consumer that needs narrowing keys off the per-member schemas.

Two levels of typing:
- **Minimal:** add your `MatchConfig` / `MatchResult` models to your contract and
  narrow on `action.kind` in the client (parse the blob with your schema).
- **First-class discriminated members:** define typed wire/input/run members by
  subclassing the exported base field classes, and compose them into your own
  union. The bases (`WatchActionWireFields`, `WatchActionInputFields`,
  `WatchActionRunFields` from `openmagpie_schema.watch`) carry the common fields
  (`id`, `rank`/ids, `state`, timestamps) so your member just adds `kind` + the typed
  payload:
  ```python
  from typing import Literal
  from openmagpie_schema.watch import WatchActionRunFields

  class MatchRunWire(WatchActionRunFields):
      kind: Literal["vevra_match"] = "vevra_match"
      result: MatchResult | None = None
  ```
  Failures still carry `error` (a string) with no result, so keep the payload
  `| None` (present on SUCCEEDED, absent otherwise) exactly like the built-ins.

## Model-bearing plugins (own tables, own database)

Code-only plugins (actions, connectors, datastore *backends* on existing tables)
need nothing more than a hook. A plugin with **its own tables** needs a Django
app and a database, both configured from the environment, still without editing
core:

1. **Add the app** (comma-separated, joins `INSTALLED_APPS`). Use the app's
   **label** (`myfork_app`), not a dotted `AppConfig` path: the label is the
   routing key below, and it's also the name of the per-app logger auto-derived
   from `INSTALLED_APPS`, so a dotted path would mis-name both.
   ```bash
   OPENMAGPIE_EXTRA_APPS=myfork_app
   ```
2. **Give it a database and route its tables there** (point at a JSON file). The
   `routing` key is the Django **app label**; a dotted-path key silently never
   matches and the tables land in `default`.
   ```bash
   OPENMAGPIE_DB_CONFIG=/run/secrets/db.json
   ```
   ```json
   {
     "databases": {
       "myfork": {"NAME": "myfork", "USER": "myfork", "PASSWORD": "...", "HOST": "db", "PORT": "5432"}
     },
     "routing": {"myfork_app": "myfork"}
   }
   ```
   `PluginAppRouter` then sends `myfork_app`'s models to the `myfork` database;
   every other app stays on `default`. Run its migrations against its own DB:
   ```bash
   python manage.py migrate --database=myfork
   ```
   Core's schema and migration history are never touched, so `apps/core` stays
   byte-identical to upstream and cherry-picks cleanly. (That migrate creates a
   `django_migrations` table on the plugin DB, and `contenttypes` / `auth` tables
   are NOT created there, since those apps stay on `default`. Keep the plugin's
   models free of relations to them.)

A register hook can also route an app **at startup** with
`plugins.db.routing.route_app("myfork_app", "myfork")` (the alias must already
be a defined database, or it fails loudly). Call it only from a hook during
startup, never per request: it mutates an unlocked module-global map that the
router reads on every query. The config-file map above is the declarative
equivalent and is usually enough on its own.

**Caveat:** Django has no cross-database foreign keys or joins. A routed app's
models reference core rows **by id, not `ForeignKey`**, and you compose core +
plugin data in Python. Transactions are per-database (a failure can leave
orphaned rows; there is no cross-DB atomicity).

## Fork REST endpoints

**Direction of control.** The clean seam is core -> fork: core's drain calls your
action's `run()`, and everything your fork does with magpie data (including writing
your own database) is hidden inside it. A fork endpoint is the OTHER direction, so
keep it narrow: it serves your OWN database and touches core only for **auth and
tenant identity**. Business logic over magpie data belongs in an action, not in an
endpoint reaching into core. Read core data (a user, an account) only when your own
UI needs to render it, and only through the service layer / `get_user_model()`, never
by writing core tables (there is no cross-DB atomicity anyway).

A fork mounts its own HTTP routes without editing `conf/urls.py`, via
`OPENMAGPIE_PLUGIN_API_URLS` (comma-separated dotted urlconf module paths, like the
other `OPENMAGPIE_PLUGIN_*` module references):
```bash
OPENMAGPIE_PLUGIN_API_URLS=myfork.urls
```
Each module is included **under the API version prefix**, so it writes
version-relative routes and never repeats or hardcodes `v1`:
```python
# myfork/views.py
from rest_framework.response import Response

from plugins.api import AccountScopedAPIView   # the stable auth/tenant bridge


class RecordListView(AccountScopedAPIView):
    def get(self, request):
        # Authenticated; request.account_id is the caller's tenant. Serve YOUR db;
        # read core data read-only via services / get_user_model() if the UI needs it.
        return Response({"items": [], "account_id": request.account_id})


# myfork/urls.py
from common.urls import api_path
from . import views

urlpatterns = [api_path("records", views.RecordListView.as_view())]   # -> /v1/records
```
**Authenticate your endpoints.** DRF's default permission here is open (core sets
`DEFAULT_PERMISSION_CLASSES = []`), so a plain function view mounts an *anonymous*
endpoint. Subclass `plugins.api.AccountScopedAPIView` (the one blessed, core-stable
entry; gives `IsAuthenticated` plus `request.account_id`), or at least set
`permission_classes = [permissions.IsAuthenticated]`, and use a DRF `APIView` so the
shared bearer/cookie authentication (and the cookie path's CSRF origin check) applies
the same way it does to core's endpoints.

Use `common.urls.api_path` for optional-trailing-slash parity with core routes.
Core patterns are listed first, so a core route that actually matches is served by
core; but Django backtracks past a core include that 404s internally, so a path under
a core segment that core doesn't serve can fall through to a plugin. Pick a segment
that doesn't overlap a core one to keep this unambiguous. A plugin urlconf mounts
arbitrary view code, so treat it like the other `OPENMAGPIE_PLUGIN_*` vars: deploy
config the operator controls. Unset means no extra routes, so core's URL surface is
unchanged.

## Fail-loud config

Misconfiguration raises `ImproperlyConfigured` at boot rather than silently
misbehaving: an `OPENMAGPIE_EXTRA_APPS` name that collides with any installed app
(Django, third-party, or local) or repeats within the list, a `databases` alias
that collides with an existing connection, a `routing` entry (or a `route_app`
call) pointing at an alias no database defines, or an `OPENMAGPIE_PLUGIN_API_URLS`
entry that isn't a bare dotted urlconf module path (or repeats one). Overlapping
route paths are not detected (Django resolves first-match-wins; a core route that
matches is served by core, but a sub-path it 404s can fall through to a plugin), so a
fork must pick non-overlapping segments.

## Settings reference

| Env var | Meaning |
| --- | --- |
| `OPENMAGPIE_PLUGIN_HOOKS` | comma-separated `module:function` hook paths |
| `OPENMAGPIE_PLUGIN_ALLOW` | allowlist of `openmagpie.plugins` entry-point names (unset/empty: all on self-host, none on cloud) |
| `OPENMAGPIE_EXTRA_APPS` | comma-separated apps to add to `INSTALLED_APPS` |
| `OPENMAGPIE_DB_CONFIG` | path to a JSON file of extra `databases` + app->alias `routing` |
| `OPENMAGPIE_PLUGIN_API_URLS` | comma-separated dotted urlconf module paths a fork mounts under the API version prefix |
