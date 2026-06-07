import type { Metadata } from "next";

/** Shared brand + site data: one source of truth for every app, so name and
 * description never drift. `url` is the CURRENT app's origin, set per build via
 * NEXT_PUBLIC_SITE_URL (marketing -> openmagpie.ai, app -> its own origin). */
export const siteMeta = {
  name: "OpenMagpie",
  url: (process.env.NEXT_PUBLIC_SITE_URL ?? "https://openmagpie.ai").replace(
    /\/$/,
    "",
  ),
  description:
    "Open-source, self-hostable social listening for Reddit, Hacker News, and your feeds. Describe what you care about in natural language and OpenMagpie surfaces the threads worth your reply.",
  tagline: "Join the conversations that matter, while they're happening",
} as const;

/**
 * Compose a Next `Metadata` from the shared base, with per-app overrides.
 * Defaults: title = brand name, shared description, canonical "/", brand icons,
 * and OpenGraph/Twitter cards. `openGraph` / `twitter` overrides merge one level
 * deep, so an app can tweak just a field (e.g. its own OG title) without
 * rebuilding the whole block. The file-based opengraph-image/twitter-image are
 * still picked up automatically by Next.
 */
export function buildMetadata(overrides: Metadata = {}): Metadata {
  const {
    title: titleOverride,
    description: descriptionOverride,
    metadataBase,
    alternates,
    icons,
    openGraph,
    twitter,
    ...rest
  } = overrides;

  const title = (titleOverride as string | undefined) ?? siteMeta.name;
  const description = descriptionOverride ?? siteMeta.description;

  return {
    metadataBase: metadataBase ?? new URL(siteMeta.url),
    title,
    description,
    alternates: alternates ?? { canonical: "/" },
    icons: icons ?? { icon: "/favicon.svg", apple: "/apple-touch-icon.png" },
    openGraph: {
      type: "website",
      url: "/",
      siteName: siteMeta.name,
      title,
      description,
      ...openGraph,
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      ...twitter,
    },
    ...rest,
  };
}
