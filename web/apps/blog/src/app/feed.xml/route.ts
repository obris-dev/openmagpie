import { siteMeta } from "@magpie/api-utils/site";
import { getAllPosts } from "@/app/_lib/posts";

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
  // The blog lives under /blog (basePath). Build absolute feed URLs against the
  // origin + /blog in code, so they're correct in dev and prod without relying
  // on NEXT_PUBLIC_SITE_URL carrying the /blog segment.
  const base = `${siteMeta.url}/blog`;
  const feedUrl = `${base}/feed.xml`;

  const items = posts
    .map((post) => {
      const url = `${base}${post.href}`;
      return [
        "    <item>",
        `      <title>${escapeXml(post.title)}</title>`,
        `      <link>${escapeXml(url)}</link>`,
        `      <guid isPermaLink="true">${escapeXml(url)}</guid>`,
        `      <pubDate>${new Date(post.date + "T00:00:00Z").toUTCString()}</pubDate>`,
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
    <description>${escapeXml(siteMeta.description)}</description>
    <language>en</language>
    <atom:link href="${escapeXml(feedUrl)}" rel="self" type="application/rss+xml" />
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
