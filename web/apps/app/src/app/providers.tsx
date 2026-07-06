"use client";

import type { ReactNode } from "react";
import { MagpieThemeProvider } from "@magpie/ui";
import { AnalyticsInitializer } from "@magpie/ui/analytics";

/**
 * System / light / dark via the shared provider (next-themes' class strategy +
 * a cross-origin cookie), so a choice made here also holds on the marketing
 * site and blog. Pair with <ThemeHeadScript /> in the layout so the cookie
 * applies before first paint.
 * AnalyticsInitializer lazy-loads PostHog on mount and captures pageviews +
 * autocapture (no-op without a key); this app deploys as its own Worker, so set
 * its own NEXT_PUBLIC_POSTHOG_KEY. To tie events to a logged-in account, call
 * identifyUser(user) on session load.
 */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <MagpieThemeProvider>
      {children}
      <AnalyticsInitializer />
    </MagpieThemeProvider>
  );
}
