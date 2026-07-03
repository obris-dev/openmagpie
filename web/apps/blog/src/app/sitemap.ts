import type { MetadataRoute } from "next";
import { siteMeta } from "@magpie/api-utils/site";
import { getAllPosts } from "@/app/_lib/posts";

export default function sitemap(): MetadataRoute.Sitemap {
  const posts = getAllPosts();
  // Absolute URLs under the /blog basePath, built in code so they're correct in
  // dev and prod regardless of whether NEXT_PUBLIC_SITE_URL carries /blog.
  const base = `${siteMeta.url}/blog`;
  return [
    {
      url: base,
      changeFrequency: "weekly",
      priority: 1,
    },
    ...posts.map((post) => ({
      url: `${base}${post.href}`,
      lastModified: new Date(post.date + "T00:00:00"),
      changeFrequency: "monthly" as const,
      priority: 0.7,
    })),
  ];
}
