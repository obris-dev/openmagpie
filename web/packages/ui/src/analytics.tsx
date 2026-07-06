"use client";

import { Suspense, useEffect } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { getPostHog, captureEvent, identifyPerson } from "./posthog-client";

// Custom event names, a contract with PostHog dashboards, so they live in one
// typed place. PostHog-reserved names ($pageview, etc.) go through capture
// directly and never through track().
export const ANALYTICS_EVENT = {
  WAITLIST_SIGNUP: "waitlist_signup",
} as const;
export type AnalyticsEvent =
  (typeof ANALYTICS_EVENT)[keyof typeof ANALYTICS_EVENT];

// Autocapture masking class: mark any element whose text could be sensitive
// (account email, IP, hostname) so posthog's autocapture ignores it. A shared
// constant because a typo silently re-enables capture of that data.
export const PH_NO_CAPTURE_CLASS = "ph-no-capture";

// Fire-and-forget sync wrappers over the async client helpers, so callers don't
// have to await analytics. This is the seam: swap the backend here and callers
// stay the same.
export function identifyUser(user: { id?: string; email?: string }): void {
  void identifyPerson(user);
}

export function track(
  event: AnalyticsEvent,
  properties?: Record<string, unknown>,
): void {
  void captureEvent(event, properties);
}

// Capture a $pageview on first load and on every App Router client-side
// navigation (posthog's own capture_pageview only fires on hard loads, so SPA
// route changes would otherwise be missed). No-op without a key (getPostHog
// resolves null).
function PageviewTracker() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  useEffect(() => {
    // Build the URL from window.location, NOT usePathname(): usePathname() drops
    // the configured basePath (e.g. the blog's /blog), which would misattribute
    // the pageview and diverge from autocapture (which uses window.location).
    // pathname + searchParams stay as deps only to re-fire this on client nav.
    const { origin, pathname: path, search } = window.location;
    const url = origin + path + search;
    async function capturePageview() {
      const posthog = await getPostHog();
      posthog?.capture("$pageview", { $current_url: url });
    }
    void capturePageview();
  }, [pathname, searchParams]);
  return null;
}

// Render once near the app root. Two jobs: (1) initialize posthog on mount, which
// is what turns on autocapture + session config (not pageview-only); and (2) track
// pageviews via PageviewTracker (App Router client nav needs it). getPostHog is
// memoized, so the two effects share a single init. useSearchParams needs a
// Suspense boundary in the App Router.
export function AnalyticsInitializer() {
  useEffect(() => {
    void getPostHog();
  }, []);

  return (
    <Suspense fallback={null}>
      <PageviewTracker />
    </Suspense>
  );
}
