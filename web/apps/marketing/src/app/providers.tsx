"use client";

import type { ReactNode } from "react";
import { MagpieThemeProvider, NotificationProvider } from "@magpie/ui";
import { AnalyticsInitializer } from "@magpie/ui/analytics";

/**
 * System / light / dark via the shared provider (next-themes' class strategy +
 * a cross-origin cookie), so the marketing site, app, and blog all flip
 * together. NotificationProvider sits inside so toasts render under the active
 * theme. Pair with <ThemeHeadScript /> in the layout for a flash-free load.
 * AnalyticsInitializer lazy-loads PostHog on mount and captures pageviews +
 * autocapture (no-op without a key); this app deploys as its own Worker, so set
 * its own NEXT_PUBLIC_POSTHOG_KEY.
 */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <MagpieThemeProvider>
      <NotificationProvider>{children}</NotificationProvider>
      <AnalyticsInitializer />
    </MagpieThemeProvider>
  );
}
