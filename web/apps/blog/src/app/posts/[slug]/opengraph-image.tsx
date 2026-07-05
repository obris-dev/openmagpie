import { notFound } from "next/navigation";
import { ogImage, OG_SIZE } from "@/app/_lib/og-image";
import { getAllPosts, getPostMeta } from "@/app/_lib/posts";

export const size = OG_SIZE;
export const contentType = "image/png";
// Static alt (not per-post): a per-post alt needs generateImageMetadata, which
// forces the image URL to /opengraph-image/<id> and breaks the JSON-LD image
// reference. The card visually shows the title anyway, so a generic alt is fine.
export const alt = "OpenMagpie blog post";

// Prerendered per slug so each post's card is a static asset.
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
  // Unknown slug: 404 instead of reading the emblem off disk and 500ing.
  // (dynamicParams = false is silently stripped from metadata image routes,
  // so the guard has to live in the handler.)
  if (!meta) notFound();
  return ogImage(meta.title, meta.author ?? undefined);
}
