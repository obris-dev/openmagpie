"use client";

import type { ReactNode } from "react";
import { MagpieThemeProvider, NotificationProvider } from "@magpie/ui";

/**
 * System / light / dark via the shared provider (next-themes' class strategy +
 * a cross-origin cookie), so the marketing site, app, and blog all flip
 * together. NotificationProvider sits inside so toasts render under the active
 * theme. Pair with <ThemeHeadScript /> in the layout for a flash-free load.
 */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <MagpieThemeProvider>
      <NotificationProvider>{children}</NotificationProvider>
    </MagpieThemeProvider>
  );
}
