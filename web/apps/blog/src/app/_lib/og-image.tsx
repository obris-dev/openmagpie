import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

// Shared 1200x630 share card for the blog's file-based opengraph-image routes.
// The brand emblem is read off disk and inlined as a data URL (Satori can't
// fetch a relative path); ink + signal otherwise. Mirrors the marketing card.
export const OG_SIZE = { width: 1200, height: 630 } as const;

export async function ogImage(title: string, subtitle?: string) {
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
          background: "#111111",
          backgroundImage:
            "radial-gradient(ellipse at top left, rgba(0,183,195,0.28), transparent 55%)",
          color: "#f7f7f5",
          fontFamily: "sans-serif",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 18,
            color: "#00b7c3",
            fontSize: 26,
            letterSpacing: 4,
            textTransform: "uppercase",
          }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={emblemSrc} width={48} height={48} alt="" />
          OpenMagpie | Blog
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
              color: "rgba(247,247,245,0.7)",
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
