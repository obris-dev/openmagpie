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

export const waitlistActions = {
  /** `source` is free-form provenance (e.g. the marketing form id). */
  submit: async (email: string, source = ""): Promise<WaitlistResult> => {
    const res = await fetch(buildApiUrl(apiRoutes.waitlist), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, source }),
    });
    return { ok: res.ok, status: res.status };
  },
};
