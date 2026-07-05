import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { OG_BRAND, OG_SIZE } from "@magpie/api-utils/site";

export const alt =
  "OpenMagpie: open-source, self-hostable social listening";
export const size = OG_SIZE;
export const contentType = "image/png";

// Branded share card. The magpie peeks from the bottom-right edge (bleeding
// off-canvas, cropped by the card bounds) the same way it does on the auth
// pages. Mascot is read off disk and inlined as a data URL (Satori can't fetch
// a relative path); brand colors + system font otherwise.
export default async function OpengraphImage() {
  const mascot = await readFile(
    join(process.cwd(), "public/brand/mascot.png"),
  );
  const mascotSrc = `data:image/png;base64,${mascot.toString("base64")}`;
  const emblem = await readFile(
    join(process.cwd(), "public/brand/emblem.svg"),
  );
  const emblemSrc = `data:image/svg+xml;base64,${emblem.toString("base64")}`;

  return new ImageResponse(
    (
      <div
        style={{
          position: "relative",
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
        {/* Mascot peek, bottom-right, bleeding off the edge (cropped by bounds). */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={mascotSrc}
          width={520}
          height={431}
          alt=""
          style={{ position: "absolute", right: -64, bottom: -54 }}
        />

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 16,
            color: OG_BRAND.signal,
            fontSize: 26,
            letterSpacing: 4,
            textTransform: "uppercase",
          }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={emblemSrc} width={40} height={40} alt="" />
          OpenMagpie
        </div>

        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            marginTop: 28,
            fontSize: 60,
            fontWeight: 700,
            lineHeight: 1.05,
            maxWidth: 720,
          }}
        >
          <span>Find the conversations that matter,&nbsp;</span>
          <span style={{ color: OG_BRAND.signal }}>while they&apos;re happening.</span>
        </div>

        <div
          style={{
            marginTop: 28,
            fontSize: 27,
            color: OG_BRAND.paperMuted,
            maxWidth: 600,
          }}
        >
          Open-source, self-hostable social listening for Reddit, Hacker News,
          and your feeds.
        </div>
      </div>
    ),
    { ...size },
  );
}
