"use client";

import { ThemeProvider, useTheme } from "next-themes";
import { useEffect } from "react";
import type { ReactNode } from "react";
import {
  THEME_COOKIE_NAME,
  THEME_STORAGE_KEY,
  THEME,
  EXPLICIT_THEMES,
  type Theme,
  type ExplicitTheme,
} from "./theme-constants";

const EXPLICIT_THEME_VALUES = new Set<string>(EXPLICIT_THEMES);

// Prod: a cookie on `.openmagpie.ai` is shared across openmagpie.ai (marketing +
// the blog at /blog) and the app.openmagpie.ai subdomain.
// Dev/previews: return undefined so the cookie is host-only on `localhost` (or the
// preview host). Cookies ignore port, so one localhost cookie spans :3000/:3001/:3002.
function getCookieDomain(): string | undefined {
  if (typeof window === "undefined") return undefined;
  const { hostname } = window.location;
  if (hostname === "openmagpie.ai" || hostname.endsWith(".openmagpie.ai")) {
    return ".openmagpie.ai";
  }
  return undefined;
}

/** Persist an explicit light/dark choice to the cross-origin cookie. Called by
 * ThemeToggle alongside next-themes' setTheme so the pick follows the user
 * across every openmagpie surface. */
export function setThemeCookie(theme: ExplicitTheme): void {
  const domain = getCookieDomain();
  // The prod-domain branch is served over https, so mark the cookie Secure;
  // on localhost (http) Secure would drop it, so omit it there.
  const domainStr = domain ? `;domain=${domain};Secure` : "";
  document.cookie = `${THEME_COOKIE_NAME}=${theme}${domainStr};path=/;max-age=31536000;SameSite=Lax`;
}

function getThemeCookie(): ExplicitTheme | null {
  const match = document.cookie
    .split(";")
    .find((c) => c.trim().startsWith(`${THEME_COOKIE_NAME}=`));
  const value = match ? (match.split("=")[1]?.trim() ?? "") : "";
  // Only light/dark are honored (the cookie carries explicit picks), matching the
  // pre-paint head-script's regex so the two can't disagree; anything else is
  // ignored.
  return EXPLICIT_THEME_VALUES.has(value) ? (value as ExplicitTheme) : null;
}

// Reconciles next-themes state with the cross-origin cookie on load: the cookie
// (an explicit cross-domain choice) wins; with no cookie it does nothing.
function ThemeWatcher() {
  // Compare against `theme` (the raw setting: system/light/dark), NOT
  // resolvedTheme: with cookie=dark, theme=system, and the OS dark, resolvedTheme
  // is already "dark" so a resolvedTheme comparison would skip the sync and leave
  // the surface following the OS instead of the explicit cross-origin choice.
  const { theme, setTheme } = useTheme();

  useEffect(() => {
    // Adopt the cross-origin cookie (an explicit choice from another openmagpie
    // surface). With no cookie, do nothing: next-themes' enableSystem already
    // tracks the OS, and an explicit local choice (localStorage) must be
    // respected, not snapped back to system-tracking.
    const cookieTheme = getThemeCookie();
    if (cookieTheme && cookieTheme !== theme) setTheme(cookieTheme);
  }, [theme, setTheme]);

  return null;
}

export interface MagpieThemeProviderProps {
  children: ReactNode;
  defaultTheme?: Theme;
}

/**
 * Shared theme provider for every openmagpie web app: next-themes' class
 * strategy plus a cookie shared across origins, so a light/dark choice made on
 * one surface (marketing, app, blog) holds on the others. Pair with
 * `ThemeHeadScript` at the top of <body> to apply the cookie before first paint.
 *
 * Two intentional behaviors: (1) once a cross-origin cookie exists, the head
 * script writes it into localStorage, so a "system" user becomes pinned to that
 * explicit theme (to reset, clear the cookie AND the "theme" localStorage key).
 * (2) a toggle on one origin only reaches an already-open sibling tab on its next
 * load: cookies fire no storage event, so there is no live cross-tab sync.
 */
export function MagpieThemeProvider({
  children,
  defaultTheme = THEME.SYSTEM,
}: MagpieThemeProviderProps) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme={defaultTheme}
      storageKey={THEME_STORAGE_KEY}
      enableSystem
      disableTransitionOnChange
    >
      <ThemeWatcher />
      {children}
    </ThemeProvider>
  );
}
