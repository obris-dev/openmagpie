"use client";

import { ThemeProvider, useTheme } from "next-themes";
import { useEffect } from "react";
import type { ReactNode } from "react";

const COOKIE_NAME = "openmagpie-theme";

// Prod: a cookie on `.openmagpie.ai` is shared across openmagpie.ai, app., blog.
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
export function setThemeCookie(theme: string): void {
  const domain = getCookieDomain();
  const domainStr = domain ? `;domain=${domain}` : "";
  document.cookie = `${COOKIE_NAME}=${theme}${domainStr};path=/;max-age=31536000;SameSite=Lax`;
}

function getThemeCookie(): string | null {
  const match = document.cookie
    .split(";")
    .find((c) => c.trim().startsWith(`${COOKIE_NAME}=`));
  return match ? (match.split("=")[1]?.trim() ?? null) : null;
}

// Reconciles next-themes state with the cross-origin cookie on load: the cookie
// (an explicit cross-domain choice) wins; with no cookie, track the system theme.
function ThemeWatcher() {
  const { resolvedTheme, setTheme } = useTheme();

  useEffect(() => {
    const cookieTheme = getThemeCookie();
    if (cookieTheme) {
      if (cookieTheme !== resolvedTheme) setTheme(cookieTheme);
      return;
    }
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    function onMediaChange() {
      const systemTheme = media.matches ? "dark" : "light";
      if (resolvedTheme === systemTheme) setTheme("system");
    }
    onMediaChange();
    media.addEventListener("change", onMediaChange);
    return () => media.removeEventListener("change", onMediaChange);
  }, [resolvedTheme, setTheme]);

  return null;
}

export interface MagpieThemeProviderProps {
  children: ReactNode;
  defaultTheme?: string;
}

/**
 * Shared theme provider for every openmagpie web app: next-themes' class
 * strategy plus a cookie shared across origins, so a light/dark choice made on
 * one surface (marketing, app, blog) holds on the others. Pair with
 * `ThemeHeadScript` in the document head to apply the cookie before first paint.
 */
export function MagpieThemeProvider({
  children,
  defaultTheme = "system",
}: MagpieThemeProviderProps) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme={defaultTheme}
      enableSystem
      disableTransitionOnChange
    >
      <ThemeWatcher />
      {children}
    </ThemeProvider>
  );
}
