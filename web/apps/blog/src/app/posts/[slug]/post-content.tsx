"use client";

import { getPostModule } from "@/app/_lib/posts";

// Renders a post's MDX content through a client boundary. Next 16.2's @next/mdx
// crashes when MDX renders in the RSC server layer (its dev JSX runtime reads
// owner-stack internals that are undefined server-side); rendering client-side
// (still SSR'd to HTML for SEO) sidesteps it. The content component comes from
// the single post registry, so a post is registered in exactly one place.
export function PostContent({ slug }: { slug: string }) {
  const Content = getPostModule(slug)?.Content;
  return Content ? <Content /> : null;
}
