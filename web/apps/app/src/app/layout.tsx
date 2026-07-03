import { Poppins, Geist_Mono } from "next/font/google";
import { ThemeHeadScript } from "@magpie/ui";
import { Providers } from "./providers";
import { buildMetadata } from "@magpie/api-utils/site";
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

// Shared base (brand name title, description, icons, metadataBase). The app is
// a different surface than marketing, so it can override OG/Twitter here later.
export const metadata = buildMetadata();

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
        className="bg-paper text-ink antialiased dark:bg-ink dark:text-paper"
      >
        <ThemeHeadScript />
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
