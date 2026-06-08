# web/AGENTS.md

Conventions for the web monorepo. Cross-cutting rules live in [../AGENTS.md](../AGENTS.md).

## Layout

pnpm workspace at `web/`. Two apps, both Next.js 16 + React 19 + TypeScript + Tailwind v4:

- `apps/app`, the product UI + auth (port 3001).
- `apps/marketing`, the public landing / waitlist (port 3000).

The shared `@source` glob (see Tailwind v4 below) already covers every app, so a new app needs no per-app Tailwind wiring.

Packages:

- `@magpie/ui`, shared UI primitives (Button, Input, PasswordInput, FormField, Logo, Emblem, ...)
- `@magpie/api-utils`, route registry + fetch wrapper + zod schemas + one action per endpoint
- `@magpie/auth`, Zustand store + auth hooks
- `@magpie/tailwind-config`, Tailwind theme + source globs

## Tailwind v4

Source scanning is driven by `@source` globs in `packages/tailwind-config/theme.css`, which covers every package + every app under the workspace. Don't add per-package globs; the shared theme glob picks them up.

Theme tokens (Ink / Paper / Signal / Glow scales, fonts) live in the same file. Apps import via a relative path so workspace resolution doesn't bite (`@import "@magpie/tailwind-config"` fails silently).

## Dark mode

Class-strategy via `next-themes` (`attribute="class"`) with `@custom-variant dark (&:where(.dark, .dark *))` in `globals.css`. Render both light + dark variants and let CSS pick to avoid hydration flicker.

## `@magpie/api-utils`

### Routes

Two namespaces in `src/routes.ts`:

- `apiRoutes` mirrors the Django URL surface (`apiRoutes.auth.me`, `apiRoutes.auth.deviceSessionInfo(id)`).
- `webRoutes` mirrors the Next.js page surface (`webRoutes.login`).

`withNext(path, next)` / `safeNext(next, fallback)` standardize post-login redirect threading. `safeNext` rejects cross-origin paths, protocol-relative URLs, backslash-paths, and `/logout`.

When a route changes in Django, update `routes.ts` and `types.ts` together. TypeScript is the single source of truth on the client side.

### Schemas + types: zod is the single source

For any response shape that drives auth state or other trust-sensitive UI, define a zod schema in `src/types.ts` and infer the TS type from it:

```ts
export const AuthUserSchema = z.object({
  id: z.string(),
  email: z.string(),
  account_id: z.string(),
  created_at: z.string(),
});
export type AuthUser = z.infer<typeof AuthUserSchema>;
```

One declaration, two outputs: a TypeScript type AND a runtime `.parse(...)` validator. They can't drift.

### `apiFetchParsed` over `apiFetch<T>`

`apiFetch<T>` is a compile-time cast; it trusts the server. Use it only for write endpoints whose response body the caller ignores.

`apiFetchParsed(schema, path, opts)` runs the schema at runtime. A malformed body throws `ZodError` instead of silently flowing into the auth store. Use this for **every** auth-shaped response.

### One action per endpoint

Call sites don't thread `(schema, route, method, body)`. They call a named action that already binds those:

```ts
// in src/actions/auth.ts
export const authActions = {
  me: () => apiFetchParsed(AuthUserSchema, apiRoutes.auth.me),
  signup: (body: AuthSignupBody) =>
    apiFetchParsed(BrowserAuthResponseSchema, apiRoutes.auth.signup, {
      method: "POST",
      body,
    }),
  ...
};

// call site
const result = await authActions.signup({ email, password });
```

When you add a new endpoint, add an action. Don't sprinkle `apiFetchParsed(...)` directly across UI components.

### Fetch wrapper invariants

- `credentials: "include"` on every API call so the `auth_token` cookie ships.
- JSON in, JSON out. Throws `ApiError` (carrying `status` + parsed `body`) on non-2xx.
- Base + path are joined by `buildApiUrl(path)` (in `routes.ts`), which resolves the API origin lazily via `resolveApiBase()`. In production `resolveApiBase()` throws if `NEXT_PUBLIC_API_URL` is missing (no silent localhost); the localhost fallback applies only in dev. Resolution is lazy, so the throw surfaces on the first request, not at module load — keeping importers side-effect-free.

## `@magpie/auth`

Zustand store + two hooks:

- `useUser()` runs the `/v1/auth/me` check once per hook instance via a `phase` state machine. Returns `{ user, loading, error }`. Distinguishes 401 ("logged out") from transient failure ("don't know").
- `useRequireAuth(redirectTo)` waits for `loading=false`, only redirects on confirmed 401, never on network errors. Preserves the current URL as `?next=...`.

Cross-tab is intentionally not handled. Refresh re-runs the `/me` check.

## Async/await over promise chains

Default to `async/await` in components and hooks. `useEffect` callbacks can't be async themselves, so the pattern is an inner async function + a `cancelled` flag:

```ts
useEffect(() => {
  let cancelled = false;
  async function load() {
    try {
      const x = await someAction();
      if (!cancelled) setX(x);
    } catch (err) {
      if (cancelled) return;
      // handle
    }
  }
  load();
  return () => { cancelled = true; };
}, [deps]);
```

## State-machine values

(Cross-cutting rule from root `AGENTS.md`, restated for TS clarity.)

Phase / status values get a `const` object + derived union type from the start. Never bare string literals in match arms or status checks:

```ts
const PHASE = {
  CONFIRM: "confirm",
  COMPLETING: "completing",
  COMPLETE: "complete",
} as const;
type Phase = (typeof PHASE)[keyof typeof PHASE];
```

## Env

- `NEXT_PUBLIC_API_URL` is **required in production**. `resolveApiBase()` throws if missing.
- `NEXT_PUBLIC_API_VERSION` is the prefix string (default `"v1"`); keep in lockstep with Django's `API_VERSION_PREFIX`.
- `ASSETS_URL` (used by `apps/email-render`) is the public base for brand image URLs baked into emails — the recipient's mail client fetches them, so it must be publicly reachable. Defaults to the marketing site; dev sets it to `http://localhost:3000` (see the `web` service in `docker-compose.yml`). Set it in prod to wherever `/brand/*` is served.
