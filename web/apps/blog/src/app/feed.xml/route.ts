import { siteMeta, blogBaseUrl } from "@magpie/api-utils/site";
import { getAllPosts } from "@/app/_lib/posts";
import { BLOG_DESCRIPTION } from "@/app/_lib/blog-meta";
import { toUtcDate } from "@/app/_lib/format-date";

// RSS 2.0 feed at /feed.xml, built from the same post registry the index uses.
function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

export function GET(): Response {
  const posts = getAllPosts();
  const title = `${siteMeta.name} Blog`;
  const base = blogBaseUrl();
  const feedUrl = `${base}/feed.xml`;
  // Newest post drives the channel's last-build date (getAllPosts is sorted
  // newest-first); omitted when there are no posts.
  const lastBuildDate = posts[0]
    ? `\n    <lastBuildDate>${toUtcDate(posts[0].date).toUTCString()}</lastBuildDate>`
    : "";

  const items = posts
    .map((post) => {
      const url = `${base}${post.href}`;
      return [
        "    <item>",
        `      <title>${escapeXml(post.title)}</title>`,
        `      <link>${escapeXml(url)}</link>`,
        `      <guid isPermaLink="true">${escapeXml(url)}</guid>`,
        `      <pubDate>${toUtcDate(post.date).toUTCString()}</pubDate>`,
        post.author ? `      <dc:creator>${escapeXml(post.author)}</dc:creator>` : "",
        `      <description>${escapeXml(post.description)}</description>`,
        "    </item>",
      ]
        .filter(Boolean)
        .join("\n");
    })
    .join("\n");

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>${escapeXml(title)}</title>
    <link>${escapeXml(base)}</link>
    <description>${escapeXml(BLOG_DESCRIPTION)}</description>
    <language>en</language>
    <atom:link href="${escapeXml(feedUrl)}" rel="self" type="application/rss+xml" />${lastBuildDate}
${items}
  </channel>
</rss>
`;

  return new Response(xml, {
    headers: {
      "Content-Type": "application/rss+xml; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
}
