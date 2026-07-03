import { ogImage, OG_SIZE } from "@/app/_lib/og-image";

export const size = OG_SIZE;
export const contentType = "image/png";
export const alt = "OpenMagpie Blog";

// Share card for the blog landing (/blog). Next wires og:image + twitter:image
// to this route automatically.
export default function Image() {
  return ogImage(
    "OpenMagpie Blog",
    "Notes on social listening, open source, and joining the conversations that matter.",
  );
}
