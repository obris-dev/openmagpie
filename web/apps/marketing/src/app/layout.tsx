import type { Metadata } from "next";
import { Poppins, Geist_Mono } from "next/font/google";
import { Providers } from "./providers";
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

export const metadata: Metadata = {
  title: "OpenMagpie — join the conversations that matter, while they're live",
  description:
    "Open-source, self-hostable listening. Describe what you care about in plain language and OpenMagpie surfaces the Reddit, Hacker News, and feed conversations worth your reply.",
  icons: {
    icon: "/favicon.svg",
    apple: "/apple-touch-icon.png",
  },
};

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
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
