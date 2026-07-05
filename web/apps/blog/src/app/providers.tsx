"use client";

import type { ReactNode } from "react";
import { MagpieThemeProvider } from "@magpie/ui";

/**
 * System / light / dark via the shared provider (next-themes' class strategy +
 * a cross-origin cookie), so every surface (marketing, app, blog) flips
 * together. Pair with <ThemeHeadScript /> in the layout for a flash-free load.
 */
export function Providers({ children }: { children: ReactNode }) {
  return <MagpieThemeProvider>{children}</MagpieThemeProvider>;
}
