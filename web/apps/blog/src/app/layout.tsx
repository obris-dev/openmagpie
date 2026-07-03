import { Poppins, Geist_Mono } from "next/font/google";
import type { Metadata } from "next";
import { ThemeHeadScript } from "@magpie/ui";
import { Providers } from "./providers";
import { SiteHeader } from "./_components/SiteHeader";
import { SiteFooter } from "./_components/SiteFooter";
import { buildMetadata, siteMeta } from "@magpie/api-utils/site";
import "./globals.css";

const poppins = Poppins({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-poppins",
  display: "swap",
});

const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
  display: "swap",
});

// Per-page titles slot into "<title> - OpenMagpie Blog"; the default is the
// blog landing. The blog is served under /blog, so canonical / OG / icon URLs
// are pinned to that base (built in code against the apex origin, so they're
// correct in dev and prod without an env carrying the /blog segment).
const blogUrl = `${siteMeta.url}/blog`;

export const metadata: Metadata = buildMetadata({
  title: {
    default: `${siteMeta.name} Blog`,
    template: `%s - ${siteMeta.name} Blog`,
  },
  description:
    "Notes on social listening, open source, and joining the conversations that matter, from the team building OpenMagpie.",
  metadataBase: new URL(blogUrl),
  alternates: { canonical: blogUrl },
  openGraph: { url: blogUrl },
  icons: { icon: "/blog/favicon.svg", apple: "/blog/apple-touch-icon.png" },
});

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${poppins.variable} ${geistMono.variable}`}
    >
      <body
        suppressHydrationWarning
        className="flex min-h-dvh flex-col bg-paper text-ink antialiased dark:bg-ink dark:text-paper"
      >
        <ThemeHeadScript />
        <Providers>
          <SiteHeader />
          <main className="flex-1">{children}</main>
          <SiteFooter />
        </Providers>
      </body>
    </html>
  );
}
