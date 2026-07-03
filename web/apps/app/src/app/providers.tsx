"use client";

import type { ReactNode } from "react";
import { MagpieThemeProvider } from "@magpie/ui";

/**
 * System / light / dark via the shared provider (next-themes' class strategy +
 * a cross-origin cookie), so a choice made here also holds on the marketing
 * site and blog. Pair with <ThemeHeadScript /> in the layout so the cookie
 * applies before first paint.
 */
export function Providers({ children }: { children: ReactNode }) {
  return <MagpieThemeProvider defaultTheme="system">{children}</MagpieThemeProvider>;
}
