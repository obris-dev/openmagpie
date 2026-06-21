// Generates public/llms-full.txt: the project docs inlined into one file so a
// chat LLM (one that can't follow links) gets complete context for writing a
// feed.yaml + watch.yaml in a single paste. Git-ignored and regenerated on every
// build (see the `prebuild` script in package.json); do NOT hand-edit. The
// canonical source is the docs read below; the link-index version is /llms.txt.
//
// Mirrors the `copy-installer` script: resolves the repo root via git so it works
// from any cwd the build runs in.
import { execSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repo = execSync("git rev-parse --show-toplevel").toString().trim();
const here = dirname(fileURLToPath(import.meta.url));
const outPath = resolve(here, "..", "public", "llms-full.txt");

const read = (rel) => readFileSync(join(repo, rel), "utf8").trimEnd();

// Delimit each inlined file with an XML-style tag rather than a markdown heading:
// the source docs keep their OWN `#` headings (no stacked-H1 collision, and we
// never have to rewrite heading levels inside YAML code fences). The tag is an
// unambiguous, LLM-friendly section boundary that also records the source path,
// and renders as inert HTML so the content inside still displays normally.
const mdDoc = (rel) => `<doc path="${rel}">\n\n${read(rel)}\n\n</doc>`;
const yamlDoc = (rel) => `<doc path="${rel}">\n\n\`\`\`yaml\n${read(rel)}\n\`\`\`\n\n</doc>`;

const sections = [
  "# OpenMagpie: full documentation for LLMs",
  "Inlined copy of the OpenMagpie docs so an assistant can help you write a\n" +
    "feed.yaml + watch.yaml and the matching `magpie` CLI commands in one shot.\n" +
    "Generated from the repo at build time (do not edit by hand); the shorter\n" +
    'link index is at /llms.txt. Each source file below is wrapped in a `<doc\n' +
    'path="...">` tag.',
  mdDoc("README.md"),
  mdDoc("config/README.md"),
  mdDoc("examples/README.md"),
];

// Inline every starter's YAML, enumerated dynamically so new starters are picked
// up without editing this script.
const startersDir = join(repo, "examples", "starters");
for (const name of readdirSync(startersDir).sort()) {
  if (!statSync(join(startersDir, name)).isDirectory()) continue;
  for (const file of ["feed.yaml", "watch.yaml"]) {
    if (existsSync(join(startersDir, name, file))) {
      sections.push(yamlDoc(`examples/starters/${name}/${file}`));
    }
  }
}

writeFileSync(outPath, `${sections.join("\n\n")}\n`);
console.log(`gen-llms-full: wrote ${outPath} (${sections.length} sections)`);
