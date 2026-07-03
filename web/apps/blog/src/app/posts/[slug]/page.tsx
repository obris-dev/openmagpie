import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { siteMeta } from "@magpie/api-utils/site";
import { getAllPosts, getPostMeta } from "@/app/_lib/posts";
import { ArticleLayout } from "@/app/_components/ArticleLayout";
import { PostContent } from "./PostContent";

export function generateStaticParams() {
  return getAllPosts().map((post) => ({ slug: post.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const meta = getPostMeta(slug);
  if (!meta) return {};
  const url = `${siteMeta.url}/blog/posts/${slug}`;
  return {
    title: meta.title,
    description: meta.description,
    alternates: { canonical: url },
    openGraph: {
      type: "article",
      url,
      title: meta.title,
      description: meta.description,
      publishedTime: meta.date,
      authors: meta.author ? [meta.author] : undefined,
    },
    // Override the layout defaults so the X/Twitter card is per-post, not generic
    // (a child `twitter` replaces the parent's, so re-set the large-image card).
    twitter: {
      card: "summary_large_image",
      title: meta.title,
      description: meta.description,
    },
  };
}

export default async function PostPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const meta = getPostMeta(slug);
  if (!meta) notFound();

  const url = `${siteMeta.url}/blog/posts/${slug}`;
  // BlogPosting structured data for article rich results (headline, date, author).
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "BlogPosting",
    headline: meta.title,
    description: meta.description,
    datePublished: meta.date,
    ...(meta.author ? { author: { "@type": "Person", name: meta.author } } : {}),
    url,
    image: `${url}/opengraph-image`,
    publisher: { "@type": "Organization", name: "OpenMagpie", url: siteMeta.url },
  };

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />
      <ArticleLayout title={meta.title} date={meta.date} author={meta.author}>
        <PostContent slug={slug} />
      </ArticleLayout>
    </>
  );
}
