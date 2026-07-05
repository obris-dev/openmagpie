import { Poppins, Geist_Mono } from "next/font/google";
import type { Metadata } from "next";
import { ThemeHeadScript } from "@magpie/ui";
import { Providers } from "./providers";
import { SiteHeader } from "./_components/site-header";
import { SiteFooter } from "./_components/site-footer";
import { BLOG_DESCRIPTION } from "./_lib/blog-meta";
import {
  buildMetadata,
  siteMeta,
  blogBaseUrl,
  blogOrigin,
  BLOG_BASE_PATH,
} from "@magpie/api-utils/site";
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
// blog landing. `blogBaseUrl` (apex origin + basePath, from the shared site
// config) is the single source for canonical / OG URLs.
export const metadata: Metadata = buildMetadata({
  title: {
    default: `${siteMeta.name} Blog`,
    template: `%s - ${siteMeta.name} Blog`,
  },
  description: BLOG_DESCRIPTION,
  // blogOrigin (bare apex, /blog stripped), NOT blogBaseUrl: the file-based
  // opengraph-image route already carries the /blog basePath, and Next path-joins
  // metadataBase's pathname on top, so a /blog here would double it to
  // /blog/blog/... (404). blogOrigin shares blogBaseUrl's defensive strip, so the
  // two can't disagree if NEXT_PUBLIC_SITE_URL is mis-set to ".../blog".
  metadataBase: new URL(blogOrigin()),
  alternates: { canonical: blogBaseUrl() },
  openGraph: { url: blogBaseUrl() },
  icons: {
    icon: `${BLOG_BASE_PATH}/favicon.svg`,
    apple: `${BLOG_BASE_PATH}/apple-touch-icon.png`,
  },
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
      {/* suppressHydrationWarning here (not just on <html>): browser extensions
          like Grammarly mutate <body> attributes before hydration, which
          otherwise warns. The theme class itself lives on <html>. */}
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
