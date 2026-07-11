// Generate src/generated.ts (zod 4) from the shared contract artifact.
//
// The contract is emitted by the Python side (tools/schema_sync) into
// packages/openmagpie-schema/schema.json. This turns that one JSON Schema into
// named, cross-referencing zod schemas + inferred types, so the web validates
// API responses against the SAME definitions the server serializes. Never edit
// src/generated.ts by hand.
//
//   node scripts/generate.mjs          # write src/generated.ts
//   node scripts/generate.mjs --check  # exit non-zero if it's stale

import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { jsonSchemaToZod } from "json-schema-to-zod";

// The contract lives in the OUTER monorepo (a Python workspace); this is a
// nested pnpm workspace, so the generator reaches across the boundary. Resolve
// the repo root via git (the pattern web/apps/marketing/scripts already use)
// rather than counting `../` from this file: works from any cwd, survives the
// package moving. Computed LAZILY (only when a relative path needs anchoring), so an
// absolute-path override still works outside a git checkout (e.g. a fork's CI tarball).
const root = () => execSync("git rev-parse --show-toplevel").toString().trim();
const anchor = (p) => (isAbsolute(p) ? p : resolve(root(), p));
// A fork points these at its own SUPERSET contract + output (built by the reusable
// tools/schema_sync generator over the fork's models) so its generated.ts gains
// its typed plugin members. Unset -> the core contract + output, unchanged. A
// relative env value anchors at ROOT (like the defaults), not the cwd, so the
// result is invariant to where the command runs; an absolute value is used as-is.
//
// The two must be set together or not at all: setting only one would read a fork
// contract while overwriting core's generated.ts (or vice versa), and later flip
// core's `--check` result wrongly. Fail loud rather than cross the streams.
if (!!process.env.OPENMAGPIE_SCHEMA_JSON !== !!process.env.OPENMAGPIE_SCHEMA_TS) {
  process.stderr.write(
    "Set BOTH OPENMAGPIE_SCHEMA_JSON and OPENMAGPIE_SCHEMA_TS (a fork override), or neither (core defaults).\n",
  );
  process.exit(1);
}
const CONTRACT = process.env.OPENMAGPIE_SCHEMA_JSON
  ? anchor(process.env.OPENMAGPIE_SCHEMA_JSON)
  : join(root(), "packages/openmagpie-schema/schema.json");
// pathToFileURL (not `file://` + concat) so a `#` or `%` in the path is escaped.
const OUTPUT = process.env.OPENMAGPIE_SCHEMA_TS
  ? pathToFileURL(anchor(process.env.OPENMAGPIE_SCHEMA_TS))
  : new URL("../src/generated.ts", import.meta.url);

if (!existsSync(CONTRACT)) {
  // Named error instead of a raw ENOENT stack (e.g. a fork's OPENMAGPIE_SCHEMA_JSON
  // pointing at a not-yet-generated contract, or a bad path).
  process.stderr.write(
    `Contract not found: ${CONTRACT}\nGenerate it first (tools/schema_sync) or fix OPENMAGPIE_SCHEMA_JSON.\n`,
  );
  process.exit(1);
}
let doc;
try {
  doc = JSON.parse(readFileSync(CONTRACT, "utf8"));
} catch (err) {
  // Named error, not a bare SyntaxError, for a truncated / non-JSON contract (e.g. a
  // fork's OPENMAGPIE_SCHEMA_JSON read mid-generate). Same style as the exists-guard.
  process.stderr.write(`Contract is not valid JSON: ${CONTRACT}\n${err.message}\n`);
  process.exit(1);
}
const defs = doc.$defs;
const names = Object.keys(defs);

// Collect the $defs each def references (for topological ordering).
function refsIn(node, acc = new Set()) {
  if (node && typeof node === "object") {
    if (typeof node.$ref === "string") acc.add(node.$ref.split("/").pop());
    for (const v of Object.values(node)) refsIn(v, acc);
  }
  return acc;
}
const deps = Object.fromEntries(
  names.map((n) => [n, [...refsIn(defs[n])].filter((d) => d !== n && defs[d])]),
);

// Topological order so a const is defined before it's referenced (Kahn).
const order = [];
const placed = new Set();
let progress = true;
while (order.length < names.length && progress) {
  progress = false;
  for (const n of names) {
    if (placed.has(n)) continue;
    if (deps[n].every((d) => placed.has(d))) {
      order.push(n);
      placed.add(n);
      progress = true;
    }
  }
}
const cyclic = names.filter((n) => !placed.has(n));
order.push(...cyclic);

const constName = (node) => `${node.$ref.split("/").pop()}Schema`;

// Node-level overrides (json-schema-to-zod calls this before its own parsing):
// (1) a oneOf carrying an OpenAPI discriminator becomes a real
//     z.discriminatedUnion (the tool ignores the discriminator keyword and would
//     otherwise emit a verbose z.any().superRefine);
// (2) a $ref becomes a reference to its named const (lazy only for cycle back-edges);
// (3) a date-time string becomes zod 4's z.iso.datetime (the tool still emits the
//     deprecated z.string().datetime() even under --zodVersion 4). `offset: true`
//     mirrors the tool's default and matches the server's tz-aware ISO output.
const parserOverride = (schema) => {
  const tag = schema?.discriminator?.propertyName;
  if (tag && Array.isArray(schema.oneOf) && schema.oneOf.every((m) => m.$ref)) {
    return `z.discriminatedUnion("${tag}", [${schema.oneOf.map(constName).join(", ")}])`;
  }
  if (schema && typeof schema.$ref === "string") {
    return cyclic.length ? `z.lazy(() => ${constName(schema)})` : constName(schema);
  }
  if (schema?.type === "string" && schema.format === "date-time") {
    return "z.iso.datetime({ offset: true })";
  }
};

let out =
  "// GENERATED by @magpie/schema (node scripts/generate.mjs). Do not edit by hand.\n" +
  "// Source: openmagpie-schema (Pydantic) -> packages/openmagpie-schema/schema.json -> zod.\n" +
  'import { z } from "zod";\n\n';
for (const name of order) {
  const body = jsonSchemaToZod(defs[name], {
    parserOverride,
    module: "none",
    noImport: true,
    withJsdocs: true,
    zodVersion: "4",
  });
  out += `export const ${name}Schema = ${body};\n`;
  out += `export type ${name} = z.infer<typeof ${name}Schema>;\n\n`;
}

// Strip trailing whitespace the tool leaves on some lines so the committed
// artifact passes the repo's whitespace check (formatting hygiene, not a
// content rewrite).
out = out.replace(/[ \t]+$/gm, "");

if (process.argv.includes("--check")) {
  // Missing output (a fork running --check before its first generate) reads as
  // stale, not a raw ENOENT stack trace (mirrors the Python generator's
  // exists-guard in write_or_check).
  // EOL-agnostic compare (like the Python generator's read_text normalization): a
  // CRLF working tree / Windows checkout shouldn't false-fail, since the content is
  // what matters. `out` is authored with \n, so normalize the on-disk copy to match.
  const current = (existsSync(OUTPUT) ? readFileSync(OUTPUT, "utf8") : "").replace(/\r\n/g, "\n");
  if (current !== out) {
    // The core command is LABELED as such so a core dev has it while a fork, which
    // overrides OPENMAGPIE_SCHEMA_TS to write its OWN output, isn't misdirected.
    process.stderr.write(
      "Generated schema output is stale. Re-run the schema generator " +
        "(core: `pnpm --filter @magpie/schema generate`) and commit.\n",
    );
    process.exit(1);
  }
} else {
  const { writeFileSync } = await import("node:fs");
  const outPath = fileURLToPath(OUTPUT);
  const outDir = dirname(outPath);
  if (!existsSync(outDir)) {
    // Named error instead of a raw ENOENT stack (e.g. a fork's OPENMAGPIE_SCHEMA_TS
    // pointing into a not-yet-created directory). Mirrors the CONTRACT exists-guard
    // above and the Python generator's write_or_check empty-render guard: fail loud
    // with the cause, not a stack trace.
    process.stderr.write(
      `Output directory not found: ${outDir}\nCreate it or fix OPENMAGPIE_SCHEMA_TS.\n`,
    );
    process.exit(1);
  }
  writeFileSync(outPath, out);
  // Resolved path, not a hardcoded "src/generated.ts": a fork override writes elsewhere.
  process.stderr.write(`Wrote ${outPath} (${order.length} schemas).\n`);
}
