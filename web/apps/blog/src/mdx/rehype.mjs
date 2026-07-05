import { slugifyWithCounter } from "@sindresorhus/slugify";
import * as acorn from "acorn";
import { toString } from "mdast-util-to-string";
import { getHighlighter } from "shiki";
import { visit } from "unist-util-visit";

let highlighterPromise;

// Syntax-highlight fenced code blocks with Shiki's `css-variables` theme, whose
// token colors are wired to brand tokens in globals.css. We emit proper hast
// <span> nodes (not a `raw` HTML string) so the MDX compiler can serialize them.
function rehypeShiki() {
  return async (tree) => {
    // Memoize the PROMISE (not the resolved value) so concurrent MDX compiles
    // share one init instead of racing to create several highlighters.
    highlighterPromise ??= getHighlighter({ theme: "css-variables" });
    const highlighter = await highlighterPromise;

    visit(tree, "element", (node) => {
      if (node.tagName !== "pre" || node.children[0]?.tagName !== "code") return;

      const codeNode = node.children[0];
      const textNode = codeNode.children[0];
      if (!textNode || textNode.type !== "text") return;

      // Language from the standard `language-<lang>` class on the code element.
      const classNames = codeNode.properties?.className;
      const langClass = Array.isArray(classNames)
        ? classNames.find(
            (c) => typeof c === "string" && c.startsWith("language-"),
          )
        : undefined;
      const language = langClass
        ? langClass.slice("language-".length)
        : node.properties.language;
      if (!language) return;

      // Shiki throws on an unknown/misspelled fence language, failing the build.
      // That's intentional: surface the typo rather than ship the block unstyled.
      const lines = highlighter.codeToThemedTokens(textNode.value, language);
      const children = [];
      lines.forEach((line, index) => {
        children.push({
          type: "element",
          tagName: "span",
          properties: { className: ["line"] },
          children: line.map((token) => ({
            type: "element",
            tagName: "span",
            properties: token.color ? { style: `color:${token.color}` } : {},
            children: [{ type: "text", value: token.content }],
          })),
        });
        if (index < lines.length - 1) {
          children.push({ type: "text", value: "\n" });
        }
      });
      codeNode.children = children;
    });
  };
}

function rehypeSlugify() {
  return (tree) => {
    const slugify = slugifyWithCounter();
    visit(tree, "element", (node) => {
      if (node.tagName === "h2" && !node.properties.id) {
        node.properties.id = slugify(toString(node));
      }
    });
  };
}

function rehypeAddMDXExports(getExports) {
  return (tree) => {
    const exports = Object.entries(getExports(tree));

    for (const [name, value] of exports) {
      // Skip names the .mdx already exports itself, but keep processing the rest
      // (continue, not return; a return would drop every later export).
      const alreadyExported = tree.children.some(
        (node) =>
          node.type === "mdxjsEsm" &&
          new RegExp(`export\\s+const\\s+${name}\\s*=`).test(node.value),
      );
      if (alreadyExported) continue;

      const exportStr = `export const ${name} = ${value}`;
      tree.children.push({
        type: "mdxjsEsm",
        value: exportStr,
        data: {
          estree: acorn.parse(exportStr, {
            sourceType: "module",
            ecmaVersion: "latest",
          }),
        },
      });
    }
  };
}

function getSections(tree) {
  const sections = [];
  visit(tree, "element", (node) => {
    if (node.tagName === "h2") {
      const title = toString(node);
      const id = node.properties.id;
      if (title && id) {
        sections.push(
          `{ title: ${JSON.stringify(title)}, id: ${JSON.stringify(id)} }`,
        );
      }
    }
  });
  return `[${sections.join(", ")}]`;
}

export const rehypePlugins = [
  rehypeShiki,
  rehypeSlugify,
  [rehypeAddMDXExports, (tree) => ({ sections: getSections(tree) })],
];
