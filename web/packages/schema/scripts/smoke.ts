// Runtime smoke of the generated contract.
//
// `check` (generate.mjs --check) only diffs strings, and `typecheck` (tsc) never
// executes generated.ts, so a fresh-but-throwing module or a zod-version bump
// that breaks at RUNTIME would sail through to production. Importing this module
// runs its top-level zod builders (catching a throw-on-import), and parsing a
// fixture through each discriminated union this contract relies on confirms the
// z.discriminatedUnion construct still validates + narrows. Run in CI after the
// freshness + typecheck steps. Exit non-zero on any failure.
import {
  AuthUserSchema,
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

// A discriminator value that isn't a union member MUST be rejected, so we know
// validation is real (not a no-op that would let anything through).
check(
  "unknown kind rejected",
  !WatchActionMutationResponseSchema.safeParse({
    dry_run: false,
    action: { kind: "not_a_kind", rank: 0 },
  }).success,
);

console.log("smoke ok");
