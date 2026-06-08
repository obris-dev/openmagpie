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
 * Which offering a signup is waiting for. Mirrors the server's
 * `WaitlistCategory` (openmagpie_schema.waitlist_enums) — keep the values in
 * lockstep. `UNKNOWN` is the server-side default (asked as a delayed second
 * step), so the client only ever SENDS a real pick.
 */
export const WAITLIST_CATEGORY = {
  WEB_UI: "web_ui",
  CLOUD: "cloud",
  EITHER: "either",
} as const;
export type WaitlistCategory =
  (typeof WAITLIST_CATEGORY)[keyof typeof WAITLIST_CATEGORY];

export const waitlistActions = {
  /**
   * `source` is free-form provenance (e.g. the marketing form id). `category`
   * is optional and, because it's captured after the email, is sent on a
   * SECOND call with the same address — the endpoint is idempotent on email and
   * records the pick in place (no duplicate row, no second welcome email).
   */
  submit: async (
    email: string,
    source = "",
    category?: WaitlistCategory,
  ): Promise<WaitlistResult> => {
    const res = await fetch(buildApiUrl(apiRoutes.waitlist), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        category != null ? { email, source, category } : { email, source },
      ),
    });
    return { ok: res.ok, status: res.status };
  },
};
