// Runtime smoke of the generated contract.
//
// `check` (generate.mjs --check) only diffs strings, and `typecheck` (tsc) never
// executes generated.ts, so a fresh-but-throwing module or a zod-version bump
// that breaks at RUNTIME would sail through to production. Importing this module
// runs its top-level zod builders (catching a throw-on-import), and parsing a
// fixture through each discriminated union this contract relies on confirms the
// z.discriminatedUnion construct still validates + narrows. It also exercises the
// EXTENSIBLE unions (action-wire / run-wire / source-spec): the plugin fallback is
// accepted, an empty kind is rejected on each, and the documented client/server
// asymmetries are pinned. Run in CI after the freshness + typecheck steps. Exit
// non-zero on any failure.
//
// The built-in cases below hardcode "log" as a known built-in kind (so kind churn is
// directed there); if "log" is ever removed from WatchActionKind, retarget them.
import {
  AuthUserSchema,
  SourceInputSchema,
  WatchActionMutationResponseSchema,
  WatchActionRunViewSchema,
} from "../src/generated";

function check(label: string, ok: boolean): void {
  if (!ok) {
    console.error(`smoke FAILED: ${label}`);
    process.exit(1);
  }
}

// A plain object schema (no union): baseline that the module loaded + validates.
check(
  "AuthUser",
  AuthUserSchema.safeParse({
    id: "u1",
    email: "e@example.com",
    account_id: "a1",
    created_at: "2026-01-01T00:00:00Z",
  }).success,
);

// Action-wire discriminated union (kind -> typed config), via its response
// envelope. `log` config is nullable, so this is the minimal valid member.
check(
  "action-wire union (log)",
  WatchActionMutationResponseSchema.safeParse({
    dry_run: false,
    action: { kind: "log", rank: 0 },
  }).success,
);

// Run-wire discriminated union (kind -> typed result | null), via the run
// detail view. A pending run carries no result.
check(
  "run-wire union (log)",
  WatchActionRunViewSchema.safeParse({
    run: {
      kind: "log",
      id: "r1",
      watch_id: "w1",
      action_id: "a1",
      feed_item_id: "f1",
      state: "pending",
    },
  }).success,
);

// The union is extensible: a NON-built-in ("plugin") kind is now ACCEPTED via the
// left-to-right plugin fallback member (config carried as an open blob). This is
// the whole point of the extensible union. Pinned on ALL THREE fallback unions:
// action-wire (here), run-wire, and source-spec (below), since each has its own
// plugin member.
check(
  "plugin kind accepted via action-wire fallback",
  WatchActionMutationResponseSchema.safeParse({
    dry_run: false,
    action: { kind: "custom_plugin_kind", rank: 0 },
  }).success,
);
check(
  "plugin kind accepted via run-wire fallback",
  WatchActionRunViewSchema.safeParse({
    run: {
      kind: "custom_plugin_kind",
      id: "r1",
      watch_id: "w1",
      action_id: "a1",
      feed_item_id: "f1",
      state: "succeeded",
      result: { anything: 1 },
    },
  }).success,
);
check(
  "plugin source kind accepted via source-spec fallback",
  SourceInputSchema.safeParse({ spec: { kind: "custom_source", name: "X" } }).success,
);

// Validation is still real (not a no-op): an empty kind fails the fallback's
// min-length, and a built-in kind with a NON-OBJECT config matches neither its typed
// member nor the fallback (whose config must be an object), so it's rejected. The
// three fallback members are structurally independent, so pin the min-length rejection
// on EACH union (else a regeneration neutering .min(1) on run-wire or source-spec
// would slip past a check that only exercised action-wire).
check(
  "empty kind rejected (action-wire)",
  !WatchActionMutationResponseSchema.safeParse({
    dry_run: false,
    action: { kind: "", rank: 0 },
  }).success,
);
check(
  "empty kind rejected (run-wire)",
  !WatchActionRunViewSchema.safeParse({
    run: { kind: "", id: "r1", watch_id: "w1", action_id: "a1", feed_item_id: "f1", state: "succeeded" },
  }).success,
);
check(
  "empty kind rejected (source-spec)",
  !SourceInputSchema.safeParse({ spec: { kind: "" } }).success,
);
check(
  "built-in kind with non-object config rejected",
  !WatchActionMutationResponseSchema.safeParse({
    dry_run: false,
    action: { kind: "log", rank: 0, config: "not-an-object" },
  }).success,
);

// KNOWN BOUNDARY (documented, not a live bug): the plugin fallback member validates
// `kind` as only z.string().min(1) and `config` as an unconstrained object, so two
// shapes the SERVER rejects are ACCEPTED here by sliding into that fallback:
//   1. a built-in kind with an OBJECT-shaped but type-invalid config, and
//   2. a whitespace-padded (or -only) kind, e.g. "log " or a disguised built-in.
// Client-side zod can't express "kind must NOT be a built-in / must be trimmed" on the
// fallback without losing the string / min-length constraints, so the exclusions the
// server enforces aren't mirrored. Safe: the server is authoritative for writes and
// rejects both, never EMITS them (a corrupt config degrades to config=null), and no
// web runtime parses these unions yet. These checks pin the behavior so a change shows.
// Pinned on the action-wire + source-spec fallbacks (the two WRITE surfaces). The
// run-wire fallback is deliberately exempt: runs are server-emitted + read-only, so a
// client never authors one and there's no write asymmetry to pin.
check(
  "built-in kind with object-shaped bad config accepted client-side (server rejects)",
  WatchActionMutationResponseSchema.safeParse({
    dry_run: false,
    action: { kind: "log", rank: 0, config: { prefix: { not: "a string" } } },
  }).success,
);
// Same object-shaped asymmetry on the source-spec write surface: a built-in kind with a
// type-invalid field (url should be a string) slides into the open fallback and is
// accepted here, though the server's typed rss member rejects it.
check(
  "built-in source kind with type-invalid field accepted client-side (server rejects)",
  SourceInputSchema.safeParse({ spec: { kind: "rss", url: 123 } }).success,
);
check(
  "whitespace-padded built-in kind accepted client-side (server rejects)",
  WatchActionMutationResponseSchema.safeParse({
    dry_run: false,
    action: { kind: "log ", rank: 0 },
  }).success,
);
// Same asymmetry on the source-spec fallback: a whitespace-padded (disguised built-in)
// source kind is accepted here but rejected by the server.
check(
  "whitespace-padded source kind accepted client-side (server rejects)",
  SourceInputSchema.safeParse({ spec: { kind: "rss " } }).success,
);

console.log("smoke ok");
