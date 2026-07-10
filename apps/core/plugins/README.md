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

**What's pluggable today.** This app ships the generic `Registry[T]` primitive and
the loader; it does **not** migrate core's existing registries. So a **new
category** you define with `Registry[T]` is fully drop-in, but a new **kind inside
an existing category** (e.g. a new action kind) is not yet: the config-layer
registry (`watches.registry`) has no dynamic `register()`, and its write-time
`KNOWN_KINDS` gate is frozen at import, so the server would reject an
unregistered kind. Making an existing category pluggable end-to-end (dynamic
config registration + a recomputed gate, plus extending the `schema_sync`
contract) is a follow-up.

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

## Fail-loud config

Misconfiguration raises `ImproperlyConfigured` at boot rather than silently
misbehaving: an `OPENMAGPIE_EXTRA_APPS` name that collides with any installed app
(Django, third-party, or local) or repeats within the list, a `databases` alias
that collides with an existing connection, a `routing` entry (or a `route_app`
call) pointing at an alias no database defines.

## Settings reference

| Env var | Meaning |
| --- | --- |
| `OPENMAGPIE_PLUGIN_HOOKS` | comma-separated `module:function` hook paths |
| `OPENMAGPIE_PLUGIN_ALLOW` | allowlist of `openmagpie.plugins` entry-point names (unset/empty: all on self-host, none on cloud) |
| `OPENMAGPIE_EXTRA_APPS` | comma-separated apps to add to `INSTALLED_APPS` |
| `OPENMAGPIE_DB_CONFIG` | path to a JSON file of extra `databases` + app->alias `routing` |
