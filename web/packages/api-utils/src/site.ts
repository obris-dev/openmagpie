import type { Metadata } from "next";

const stripTrailingSlash = (url: string) => url.replace(/\/$/, "");

// Reduce a URL to its bare origin (scheme + host + port), dropping any path,
// query, or hash. Guards against a mis-set ".../blog" value that would otherwise
// double-prefix once a basePath is appended (this repo has shipped one such bug).
// Requires an http(s) scheme: a scheme-less value like "localhost:3002" parses as
// an opaque URL whose .origin is the string "null" (which would silently ship
// "null/blog"), so reject anything that isn't http/https.
function toBareOrigin(value: string): string {
  const url = new URL(value);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error(
      `Expected an http(s) origin, got ${JSON.stringify(value)} (missing scheme? use e.g. https://example.com).`,
    );
  }
  return url.origin;
}

/** Shared brand + site data: one source of truth for every app, so name and
 * description never drift. `url` is the CURRENT app's origin, set per build via
 * NEXT_PUBLIC_SITE_URL (marketing -> openmagpie.ai, app -> its own origin). */
export const siteMeta = {
  name: "OpenMagpie",
  url: stripTrailingSlash(
    process.env.NEXT_PUBLIC_SITE_URL ?? "https://openmagpie.ai",
  ),
  repoUrl: "https://github.com/obris-dev/openmagpie",
  description:
    "Open-source, self-hostable social listening for Reddit, Hacker News, and your feeds. Describe what you care about in natural language and OpenMagpie surfaces the threads worth your reply.",
  tagline: "Join the conversations that matter, while they're happening",
} as const;

// Brand palette for OG / share cards. Satori inlines these as plain color
// strings; shared so the blog and marketing cards stay visually identical.
export const OG_BRAND = {
  ink: "#111111",
  signal: "#00b7c3",
  paper: "#f7f7f5",
  glow: "rgba(0, 183, 195, 0.28)",
  paperMuted: "rgba(247, 247, 245, 0.7)",
} as const;

// Standard OG / share-card dimensions (1.91:1). Shared so every card is one size.
export const OG_SIZE = { width: 1200, height: 630 } as const;

// The blog app is served under this path (its Next config sets the same value as
// basePath). Single home for the literal so canonicals, cross-app links, sitemap,
// feed, and robots never drift from each other.
export const BLOG_BASE_PATH = "/blog";

// The CURRENT app's own blog base: this app's origin + /blog, from
// NEXT_PUBLIC_SITE_URL. For the blog's canonical / OG / sitemap / feed and the
// layout's metadataBase. Lazy (like origins) so importing this module stays
// side-effect-free. toBareOrigin drops any path so a mis-set
// NEXT_PUBLIC_SITE_URL=".../blog" can't double-prefix, and metadataBase +
// blogBaseUrl derive from the same normalized origin so they can't disagree.
//
// VALID ONLY in builds where this app serves the apex (the blog itself, and
// marketing's robots). NOT a cross-app link: to link TO the blog from another
// app use blogLinkUrl() (which reads NEXT_PUBLIC_BLOG_URL). In an apps/app build
// these would wrongly yield app.openmagpie.ai/blog.
export function blogOrigin(): string {
  return toBareOrigin(siteMeta.url);
}
export function blogBaseUrl(): string {
  return `${blogOrigin()}${BLOG_BASE_PATH}`;
}

// Resolve a sibling app's origin at CALL time (lazy, mirroring resolveApiBase in
// routes.ts). Throws in production if its env is unset (otherwise a misconfigured
// prod build silently bakes localhost into cross-app links), and falls back to the
// dev port in dev. toBareOrigin drops any path so a value like ".../blog" can't
// double-prefix once BLOG_BASE_PATH is appended. Lazy so importing this module
// doesn't read any env until a URL is actually built (blogOrigin/blogBaseUrl
// above are lazy for the same reason).
function resolveOrigin(
  value: string | undefined,
  devFallback: string,
  envName: string,
): string {
  const raw = value?.trim();
  if (raw) return toBareOrigin(raw);
  if (process.env.NODE_ENV === "production") {
    throw new Error(
      `${envName} is required in production. Set it at build time to the app's bare origin (scheme + host, no path).`,
    );
  }
  return devFallback;
}

/**
 * Origins of the sibling web apps, for cross-app links (e.g. marketing -> blog).
 * Each is a lazy resolver returning a BARE origin (scheme + host, no path), so
 * `new URL(path, origins.x())` is safe and a missing prod env fails loudly rather
 * than baking localhost. In prod the app is on its subdomain (app.openmagpie.ai);
 * the blog shares the apex origin and lives at BLOG_BASE_PATH.
 */
export const origins = {
  marketing: () =>
    resolveOrigin(
      process.env.NEXT_PUBLIC_MARKETING_URL,
      "http://localhost:3000",
      "NEXT_PUBLIC_MARKETING_URL",
    ),
  app: () =>
    resolveOrigin(
      process.env.NEXT_PUBLIC_APP_URL,
      "http://localhost:3001",
      "NEXT_PUBLIC_APP_URL",
    ),
  blog: () =>
    resolveOrigin(
      process.env.NEXT_PUBLIC_BLOG_URL,
      "http://localhost:3002",
      "NEXT_PUBLIC_BLOG_URL",
    ),
} as const;

// Cross-app LINK to the blog: its origin + basePath (dev localhost:3002/blog,
// prod openmagpie.ai/blog). Lazy (calls origins.blog()); carries a path, so it's
// a link href, NOT a base for `new URL(...)`. origins.blog() already throws in
// prod if NEXT_PUBLIC_BLOG_URL is unset. We deliberately do NOT cross-check it
// against blogOrigin: that's the CALLING app's own origin (SITE_URL), which is
// only the apex for apex apps, so the comparison would false-throw the moment a
// non-apex app (e.g. apps/app) links to the blog with a correct config.
export function blogLinkUrl(): string {
  return `${origins.blog()}${BLOG_BASE_PATH}`;
}

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
