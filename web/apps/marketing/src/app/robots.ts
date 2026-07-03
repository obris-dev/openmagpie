import type { MetadataRoute } from "next";
import { siteMeta } from "@magpie/api-utils/site";

export default function robots(): MetadataRoute.Robots {
  // Marketing owns the apex robots.txt (openmagpie.ai/robots.txt), so it also
  // points crawlers at the blog's sitemap. The blog is a separate app served
  // under /blog, so its own robots route wouldn't be read at the apex.
  return {
    rules: { userAgent: "*", allow: "/" },
    sitemap: [`${siteMeta.url}/sitemap.xml`, `${siteMeta.url}/blog/sitemap.xml`],
  };
}
