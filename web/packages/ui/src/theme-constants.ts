// One home for the theme contract shared by the provider, the toggle, and the
// blocking head-script. These three must agree on the cookie name, the
// localStorage key, and the theme values; a bare literal drifting in any one of
// them silently reintroduces the first-paint flash this feature exists to
// prevent. The head-script interpolates these (trusted constants, so the inline
// script stays injection-safe).

export const THEME_COOKIE_NAME = "openmagpie-theme";

// next-themes defaults its localStorage key to "theme"; we pin it explicitly
// (passed as `storageKey` on the provider) so the head-script isn't depending on
// an implicit default it hardcodes separately.
export const THEME_STORAGE_KEY = "theme";

export const THEME = {
  LIGHT: "light",
  DARK: "dark",
  SYSTEM: "system",
} as const;

export type Theme = (typeof THEME)[keyof typeof THEME];

// Values that persist to the cross-origin cookie: explicit picks only. The
// pre-paint head-script matches exactly these, so keep the two in lockstep.
// "system" is intentionally excluded (it means "follow the OS", which each
// surface already does natively, so there is nothing to carry across origins).
// These must stay regex-safe: the head-script interpolates them into its cookie
// matcher, so a value containing a regex metacharacter would corrupt that match.
export const EXPLICIT_THEMES = [THEME.LIGHT, THEME.DARK] as const;
export type ExplicitTheme = (typeof EXPLICIT_THEMES)[number];
