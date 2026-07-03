import { ogImage, OG_SIZE } from "@/app/_lib/og-image";
import { getAllPosts, getPostMeta } from "@/app/_lib/posts";

export const size = OG_SIZE;
export const contentType = "image/png";
export const alt = "OpenMagpie blog post";

// Per-post share card (the post title on the brand card). Prerendered per slug.
export function generateStaticParams() {
  return getAllPosts().map((post) => ({ slug: post.slug }));
}

export default async function Image({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const meta = getPostMeta(slug);
  return ogImage(meta?.title ?? "OpenMagpie Blog", meta?.author ?? undefined);
}
