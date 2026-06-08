// Shared brand tokens for the email templates. Mirrors the locked OpenMagpie
// palette (teal `signal` on ink/paper neutrals) so transactional mail looks
// like the product. Kept inline here rather than importing the Tailwind theme
// CSS — React Email's <Tailwind> takes a JS config object, not a CSS file.

export const emailTailwindConfig = {
  theme: {
    extend: {
      colors: {
        signal: "#00b7c3",
        "signal-dark": "#00777f",
        ink: "#111111",
        "ink-muted": "#4b5158",
        paper: "#f7f7f5",
        "paper-soft": "#f3f1ea",
      },
      fontFamily: {
        sans: ["Poppins", "Helvetica", "Arial", "sans-serif"],
      },
    },
  },
};

// Canonical marketing/site URL — the single source for the public openmagpie.ai
// origin used as a default below and in templates.
export const SITE_URL = "https://openmagpie.ai";

// Absolute base for brand assets referenced in email (the recipient's mail
// client fetches these, so it must be publicly reachable — no relative paths).
// Prod default is the marketing site; dev sets ASSETS_URL to the local
// marketing origin (see the web service env in docker-compose).
export const ASSETS_URL = (process.env.ASSETS_URL || SITE_URL).replace(/\/$/, "");

// The full "OpenMagpie" wordmark (emblem + name), on-light variant for the
// white email surface. PNG, NOT the source SVG — email clients (Gmail, Outlook)
// don't render SVG. Rasterized from wordmark-on-light.svg (633x120, ~5.27:1).
export const WORDMARK_URL = `${ASSETS_URL}/brand/wordmark-on-light.png`;

// Poppins (the product's display font) for React Email's <Font>, with a
// Helvetica fallback for clients that don't load web fonts.
export const poppins = {
  fontFamily: "Poppins",
  fallbackFontFamily: "Helvetica" as const,
  webFont: {
    url: "https://fonts.gstatic.com/s/poppins/v21/pxiByp8kv8JHgFVrLGT9Z1xlFd2JQEk.woff2",
    format: "woff2" as const,
  },
  fontWeight: 600,
  fontStyle: "normal" as const,
};
