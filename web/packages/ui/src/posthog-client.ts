"use client";

import type { CaptureResult, PostHog } from "posthog-js";

// Query-string keys kept on URLs sent to analytics; every other param is dropped
// before the event leaves the browser. Attribution params are safe to keep;
// dropping the rest keeps sensitive query params (the app's ?next= redirect
// targets, token-bearing links) out of analytics.
const ANALYTICS_QUERY_ALLOWLIST = [
  "utm_source",
  "utm_medium",
  "utm_campaign",
  "utm_term",
  "utm_content",
  "gclid",
  "fbclid",
  "ref",
] as const;

// Keep only the allowlisted query params; drop the rest + the fragment. NOTE:
// this scrubs the QUERY STRING only. A secret in a PATH segment (/verify/<token>)
// is out of scope, so don't route secrets through the path and expect this to
// hide them.
function sanitizeUrl(value: string): string {
  try {
    const url = new URL(value);
    const kept = new URLSearchParams();
    url.searchParams.forEach((paramValue, paramKey) => {
      if ((ANALYTICS_QUERY_ALLOWLIST as readonly string[]).includes(paramKey)) {
        kept.set(paramKey, paramValue);
      }
    });
    url.search = kept.toString();
    url.hash = "";
    return url.toString();
  } catch {
    // Unparseable: drop anything from the first query/fragment marker on.
    const cut = value.search(/[?#]/);
    return cut === -1 ? value : value.slice(0, cut);
  }
}

function scrubUrlField(bag: Record<string, unknown>, key: string): void {
  const value = bag[key];
  if (typeof value === "string") bag[key] = sanitizeUrl(value);
}

// posthog serializes autocapture DOM into a chain string; a clicked link's href
// rides along as attr__href="URL" (and legacy href="URL"). URLs never contain a
// literal unescaped ", so match up to the next quote.
const HREF_ATTR_RE = /((?:attr__)?href)="([^"]*)"/g;
function scrubElementsChain(chain: string): string {
  return chain.replace(
    HREF_ATTR_RE,
    (_match, attr: string, url: string) => `${attr}="${sanitizeUrl(url)}"`,
  );
}

// Scrub every URL that can carry a query string out of an event before it sends.
// before_send is posthog's recommended hook and sees the fully-built event
// (sanitize_properties is deprecated). Query-string scrub only; path-segment
// secrets are out of scope (see sanitizeUrl). Carriers, all stamped by default:
//   - page URL + referrer;
//   - session-entry URL + referrer (the session's first URL, put on every event);
//   - person-property bags ($set / $set_once): the $initial_* first-touch URLs
//     AND the unprefixed $current_url / $referrer;
//   - autocapture clicked-element hrefs ($elements array + $elements_chain);
//   - $external_click_url (top-level raw href of a cross-host link click).
// Not scrubbed: nested URLs in $web_vitals / $$heatmap payloads are
// remote-config-gated and off by default; scrub them here if you enable either.
function scrubEvent(event: CaptureResult | null): CaptureResult | null {
  const props = event?.properties;
  if (!props) return event;

  scrubUrlField(props, "$current_url");
  scrubUrlField(props, "$referrer");
  scrubUrlField(props, "$session_entry_url");
  scrubUrlField(props, "$session_entry_referrer");
  scrubUrlField(props, "$external_click_url");

  for (const bagKey of ["$set", "$set_once"] as const) {
    const bag = props[bagKey];
    if (bag && typeof bag === "object") {
      const record = bag as Record<string, unknown>;
      scrubUrlField(record, "$current_url");
      scrubUrlField(record, "$referrer");
      scrubUrlField(record, "$initial_current_url");
      scrubUrlField(record, "$initial_referrer");
    }
  }

  if (typeof props.$elements_chain === "string") {
    props.$elements_chain = scrubElementsChain(props.$elements_chain);
  }
  if (Array.isArray(props.$elements)) {
    for (const element of props.$elements) {
      if (element && typeof element === "object") {
        scrubUrlField(element as Record<string, unknown>, "href");
        scrubUrlField(element as Record<string, unknown>, "attr__href");
      }
    }
  }

  return event;
}

let posthogInstance: PostHog | null = null;
let posthogLoading: Promise<PostHog | null> | null = null;

// Lazy-load posthog-js on first use so it's code-split out of the initial bundle.
// Returns null (and never loads) when NEXT_PUBLIC_POSTHOG_KEY is unset, so dev,
// previews, and any build without the key are a complete no-op.
export async function getPostHog(): Promise<PostHog | null> {
  if (posthogInstance) return posthogInstance;
  if (posthogLoading) return posthogLoading;
  posthogLoading = loadPostHog();
  return posthogLoading;
}

async function loadPostHog(): Promise<PostHog | null> {
  const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
  if (!key) return null;
  try {
    const { default: posthog } = await import("posthog-js");
    posthog.init(key, {
      // api_host is where events are sent: point it at a first-party reverse
      // proxy (e.g. https://a.openmagpie.ai) to serve analytics from your own
      // domain. ui_host keeps the toolbar + "view in PostHog" links pointing at
      // the real app even when ingestion rides the proxy.
      api_host:
        process.env.NEXT_PUBLIC_POSTHOG_HOST ?? "https://us.i.posthog.com",
      ui_host:
        process.env.NEXT_PUBLIC_POSTHOG_UI_HOST ?? "https://us.posthog.com",
      // Anonymous marketing / blog traffic still emits events (they power Web
      // Analytics, trends, and paths); we just don't create a stored person
      // profile per anonymous visitor. Flip to "always" for a profile per visitor.
      person_profiles: "identified_only",
      // Pageviews are captured manually by <AnalyticsInitializer /> so App Router
      // client-side navigations are counted (not just the first load); autocapture
      // (clicks / inputs) stays on. Pageleave gives session duration + bounce.
      capture_pageview: false,
      capture_pageleave: true,
      // Scrub URL query strings out of every outgoing event (see scrubEvent).
      before_send: scrubEvent,
    });
    posthogInstance = posthog;
    return posthog;
  } catch (error) {
    console.error("Failed to load PostHog:", error);
    // Clear the memo so a later call can retry. Best-effort: bundlers cache a
    // rejected dynamic import(), so this mainly recovers when init() itself
    // threw, not when the chunk fetch failed. The no-key return stays permanent.
    posthogLoading = null;
    return null;
  }
}

// PostHog only needs the distinct id; email is optional (a session without one
// still identifies rather than staying permanently anonymous).
export async function identifyPerson(user: {
  id?: string;
  email?: string;
}): Promise<void> {
  if (!user?.id) return;
  const posthog = await getPostHog();
  posthog?.identify(user.id, user.email ? { email: user.email } : undefined);
}

export async function captureEvent(
  event: string,
  properties: Record<string, unknown> = {},
): Promise<void> {
  const posthog = await getPostHog();
  posthog?.capture(event, properties);
}
