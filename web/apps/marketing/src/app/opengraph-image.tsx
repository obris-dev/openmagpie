import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

export const alt =
  "OpenMagpie — open-source, self-hostable social listening";
export const size = { width: 1200, height: 630 };
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
          background: "#111111",
          backgroundImage:
            "radial-gradient(ellipse at top left, rgba(0,183,195,0.28), transparent 55%)",
          color: "#f7f7f5",
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
            color: "#00b7c3",
            fontSize: 26,
            letterSpacing: 4,
            textTransform: "uppercase",
          }}
        >
          <div
            style={{
              width: 16,
              height: 16,
              borderRadius: 999,
              background: "#00b7c3",
            }}
          />
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
          <span style={{ color: "#00b7c3" }}>while they&apos;re happening.</span>
        </div>

        <div
          style={{
            marginTop: 28,
            fontSize: 27,
            color: "rgba(247,247,245,0.7)",
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
