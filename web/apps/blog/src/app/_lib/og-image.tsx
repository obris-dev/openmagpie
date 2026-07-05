import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { siteMeta, OG_BRAND, OG_SIZE } from "@magpie/api-utils/site";

// Re-export the shared size so the file-based opengraph-image routes import both
// `size` and `ogImage` from here.
export { OG_SIZE };

// 1200x630 share card for the blog's file-based opengraph-image routes. The brand
// emblem is read off disk and inlined as a data URL (Satori can't fetch a relative
// path); ink + signal otherwise. Mirrors the marketing card.

export async function ogImage(title: string, subtitle?: string) {
  // Reads the emblem from public/ off disk. This works because every OG route is
  // prerendered at build (generateStaticParams), where process.cwd() is the app
  // root with public/ present. It would NOT work in an on-demand worker render,
  // so the OG routes must stay static (see the guard in [slug]/opengraph-image).
  const emblem = await readFile(
    join(process.cwd(), "public/brand/emblem.svg"),
  );
  const emblemSrc = `data:image/svg+xml;base64,${emblem.toString("base64")}`;

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "72px",
          background: OG_BRAND.ink,
          backgroundImage: `radial-gradient(ellipse at top left, ${OG_BRAND.glow}, transparent 55%)`,
          color: OG_BRAND.paper,
          fontFamily: "sans-serif",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 18,
            color: OG_BRAND.signal,
            fontSize: 26,
            letterSpacing: 4,
            textTransform: "uppercase",
          }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={emblemSrc} width={48} height={48} alt="" />
          {siteMeta.name} | Blog
        </div>

        <div
          style={{
            display: "flex",
            marginTop: 28,
            fontSize: 64,
            fontWeight: 700,
            lineHeight: 1.05,
            maxWidth: 1000,
          }}
        >
          {title}
        </div>

        {subtitle ? (
          <div
            style={{
              display: "flex",
              marginTop: 28,
              fontSize: 30,
              color: OG_BRAND.paperMuted,
              maxWidth: 860,
            }}
          >
            {subtitle}
          </div>
        ) : null}
      </div>
    ),
    { ...OG_SIZE },
  );
}
