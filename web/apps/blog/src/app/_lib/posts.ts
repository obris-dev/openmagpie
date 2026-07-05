import type { ComponentType } from "react";
import { toUtcDate } from "./format-date";
import FirstUsers, {
  meta as firstUsersMeta,
  sections as firstUsersSections,
} from "@/posts/get-first-users-no-marketing-budget.mdx";

// Single post registry: one entry per post (its content component + meta + the
// rehype-generated section list) is the only place you register a post. Server
// code (index, sitemap, RSS, the TOC) reads meta/sections; the content renders
// through the [slug] route's client boundary (PostContent).
export type PostMeta = {
  title: string;
  date: string;
  description: string;
  author?: string;
};

// One heading in a post's table of contents (h2s, slugified id), from rehype.
export type Section = { title: string; id: string };

type PostModule = {
  slug: string;
  meta: PostMeta;
  Content: ComponentType;
  sections: Section[];
};

const postModules: PostModule[] = [
  {
    slug: "get-first-users-no-marketing-budget",
    meta: firstUsersMeta,
    Content: FirstUsers,
    sections: firstUsersSections,
  },
];

// Fail the build if any post's meta is incomplete or malformed, so a post can
// never ship with a degraded card (generic description, empty/invalid date).
// Runs at module load, which the build imports -> a bad post breaks the build,
// not production. `.mdx` meta isn't typechecked against PostMeta, so this is
// what actually enforces the shape.
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
for (const { slug, meta } of postModules) {
  const problems: string[] = [];
  if (!meta.title?.trim()) problems.push("title");
  if (!meta.description?.trim()) problems.push("description");
  if (!ISO_DATE.test(meta.date ?? "")) {
    problems.push(`date (need YYYY-MM-DD, got ${JSON.stringify(meta.date)})`);
  } else if (Number.isNaN(toUtcDate(meta.date).getTime())) {
    // Shape is right but it's not a real calendar date (e.g. 2026-13-45), which
    // would otherwise throw a cryptic RangeError in the sitemap's toISOString().
    problems.push(`date (not a real calendar date: ${JSON.stringify(meta.date)})`);
  }
  if (problems.length > 0) {
    throw new Error(
      `Blog post "${slug}" has invalid meta [${problems.join(", ")}]. ` +
        `Fix its "export const meta" in src/posts/${slug}.mdx.`,
    );
  }
}

export type Post = {
  slug: string;
  href: string;
  title: string;
  date: string;
  description: string;
  author: string | null;
};

function toPost({ slug, meta }: PostModule): Post {
  // title / date / description are guaranteed present by the validation above.
  return {
    slug,
    href: `/posts/${slug}`,
    title: meta.title,
    date: meta.date,
    description: meta.description,
    author: meta.author ?? null,
  };
}

export function getAllPosts(): Post[] {
  return postModules
    .map(toPost)
    .sort((a, b) => toUtcDate(b.date).getTime() - toUtcDate(a.date).getTime());
}

export function getPostModule(slug: string): PostModule | undefined {
  return postModules.find((p) => p.slug === slug);
}

export function getPostMeta(slug: string): PostMeta | undefined {
  return getPostModule(slug)?.meta;
}
