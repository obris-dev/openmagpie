import type { MetadataRoute } from "next";
import { blogBaseUrl } from "@magpie/api-utils/site";
import { getAllPosts } from "@/app/_lib/posts";
import { toUtcDate } from "@/app/_lib/format-date";

export default function sitemap(): MetadataRoute.Sitemap {
  const posts = getAllPosts();
  const base = blogBaseUrl();
  return [
    {
      url: base,
      changeFrequency: "weekly",
      priority: 1,
    },
    ...posts.map((post) => ({
      url: `${base}${post.href}`,
      lastModified: toUtcDate(post.date),
      changeFrequency: "monthly" as const,
      priority: 0.7,
    })),
  ];
}
