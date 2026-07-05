import { ogImage, OG_SIZE } from "@/app/_lib/og-image";
import { BLOG_DESCRIPTION } from "@/app/_lib/blog-meta";
import { siteMeta } from "@magpie/api-utils/site";

export const size = OG_SIZE;
export const contentType = "image/png";
export const alt = `${siteMeta.name} Blog`;

// Share card for the blog landing (/blog). Next wires og:image + twitter:image
// to this route automatically.
export default function Image() {
  return ogImage(`${siteMeta.name} Blog`, BLOG_DESCRIPTION);
}
