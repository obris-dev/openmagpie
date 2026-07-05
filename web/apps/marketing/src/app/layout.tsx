import { Poppins, Geist_Mono } from "next/font/google";
import { ThemeHeadScript } from "@magpie/ui";
import { Providers } from "./providers";
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

// Marketing leads with the tagline title; base + OG/Twitter come from the
// shared composer. The file-based opengraph-image/twitter-image attach
// automatically.
export const metadata = buildMetadata({
  title: `${siteMeta.name} - ${siteMeta.tagline}`,
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
      <body className="bg-paper text-ink antialiased dark:bg-ink dark:text-paper">
        <ThemeHeadScript />
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
