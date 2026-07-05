import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { siteMeta, blogBaseUrl } from "@magpie/api-utils/site";
import { getAllPosts, getPostMeta, getPostModule } from "@/app/_lib/posts";
import { ArticleLayout } from "@/app/_components/article-layout";
import { PostContent } from "./post-content";

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
  const url = `${blogBaseUrl()}/posts/${slug}`;
  return {
    title: meta.title,
    description: meta.description,
    alternates: { canonical: url },
    openGraph: {
      type: "article",
      url,
      // A child openGraph replaces the layout's, so re-set siteName.
      siteName: siteMeta.name,
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
  const post = getPostModule(slug);
  if (!post) notFound();
  const { meta, sections } = post;

  const url = `${blogBaseUrl()}/posts/${slug}`;
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
    publisher: {
      "@type": "Organization",
      name: siteMeta.name,
      url: siteMeta.url,
    },
  };

  return (
    <>
      <script
        type="application/ld+json"
        // Escape < so a "</script>" inside any field can't break out of the tag.
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(jsonLd).replace(/</g, "\\u003c"),
        }}
      />
      <ArticleLayout
        title={meta.title}
        date={meta.date}
        author={meta.author}
        sections={sections}
      >
        <PostContent slug={slug} />
      </ArticleLayout>
    </>
  );
}
