import type { MetadataRoute } from "next";
import { siteMeta } from "@magpie/api-utils/site";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    {
      url: `${siteMeta.url}/`,
      changeFrequency: "weekly",
      priority: 1,
    },
  ];
}
