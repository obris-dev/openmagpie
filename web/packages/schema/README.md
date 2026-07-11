# @magpie/schema

Zod schemas + inferred types generated from the server's shared contract
(`openmagpie-schema` Pydantic models → `packages/openmagpie-schema/schema.json` → zod).
Never edit `src/generated.ts` by hand; run `pnpm generate` (see `scripts/generate.mjs`).

## The generated unions are LOOSER than the server; the server is authoritative

The watch-action and source-spec schemas are **extensible discriminated unions**: the
built-in kinds are typed members, plus an open plugin-fallback member (`kind:
z.string()`, config as an unconstrained object) so a fork's custom kind still parses.

That fallback can't express "any kind *except* the built-ins" in zod, so these unions
**accept some shapes the server rejects**. For example, a built-in kind (`log`) with a
type-invalid or whitespace-padded value slides into the fallback and passes `parse()`,
though the server's typed validation + write gate reject it.

So: **do not use `parse()` as a correctness gate for writes.** It's a client-side
convenience (shape hints, obvious-typo feedback); the server is the authority and
returns the real per-field errors. Reads are safe: the server only ever emits
server-valid shapes. (The exact accepted-here/rejected-there cases are pinned in
`scripts/smoke.ts`.)
