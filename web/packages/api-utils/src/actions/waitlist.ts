import { apiRoutes, buildApiUrl } from "../routes";

/**
 * Public waitlist signup. Unlike `authActions` (which go through `apiFetch`
 * with `credentials: "include"` + `ApiError`), this hits a PUBLIC endpoint, so
 * it uses a bare `fetch` with NO credentials and reports `{ok, status}` rather
 * than throwing. The URL is built via the shared `buildApiUrl(apiRoutes.waitlist)`.
 *
 * Importing this is side-effect-free (only `../routes`, which resolves the API
 * base lazily), so the marketing site can pull it via the `./waitlist` subpath
 * without dragging in the rest of the package.
 */
export interface WaitlistResult {
  ok: boolean;
  status: number;
}

/**
 * A not-yet-shipped source a signup can vote for on the confirmation card (it's
 * a MULTI-select, so a vote is a set of these). Mirrors the server's
 * `WaitlistSourceInterest` (openmagpie_schema); keep the values in lockstep.
 * `OTHER` pairs with a free-text note.
 */
export const WAITLIST_SOURCE = {
  LINKEDIN: "linkedin",
  SLACK: "slack",
  X: "x",
  BLUESKY: "bluesky",
  MASTODON: "mastodon",
  GITHUB: "github",
  OTHER: "other",
} as const;
export type WaitlistSource =
  (typeof WAITLIST_SOURCE)[keyof typeof WAITLIST_SOURCE];

export const waitlistActions = {
  /**
   * `source` is free-form provenance (e.g. the marketing form id). The optional
   * source VOTE (`sourceInterests`, a set, + free text when it includes `OTHER`)
   * is captured after the email, so it's sent on a SECOND call with the same
   * address. The endpoint is idempotent on email and records the vote in place
   * (no duplicate row, no second welcome email).
   */
  submit: async (
    email: string,
    source = "",
    sourceInterests?: WaitlistSource[],
    sourceInterestOther?: string,
  ): Promise<WaitlistResult> => {
    const body: Record<string, unknown> = { email, source };
    if (sourceInterests && sourceInterests.length > 0) {
      body.source_interests = sourceInterests;
      if (sourceInterests.includes(WAITLIST_SOURCE.OTHER) && sourceInterestOther) {
        body.source_interest_other = sourceInterestOther;
      }
    }
    const res = await fetch(buildApiUrl(apiRoutes.waitlist), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return { ok: res.ok, status: res.status };
  },
};
