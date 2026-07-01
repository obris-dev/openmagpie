import { AuthUserSchema } from "@magpie/schema";

import { apiFetch, apiFetchParsed } from "../fetch-wrapper";
import { apiRoutes } from "../routes";
import { BrowserAuthResponseSchema, DeviceSessionInfoSchema } from "../types";
import type { AuthLoginBody, AuthSignupBody } from "../types";

/**
 * One function per auth endpoint. Each one owns its route, method, and
 * response schema so call sites just say "what they want" without
 * threading routes / schemas / methods through their imports.
 *
 * Read responses (`me`, `deviceSessionInfo`) are runtime-validated via
 * Zod, a malformed body throws instead of silently flowing into the
 * auth store. Write endpoints with no body of interest (`deny`,
 * `complete`) skip parsing since callers only care about success/error.
 */
export const authActions = {
  me: () => apiFetchParsed(AuthUserSchema, apiRoutes.auth.me),

  signup: (body: AuthSignupBody) =>
    apiFetchParsed(BrowserAuthResponseSchema, apiRoutes.auth.signup, {
      method: "POST",
      body,
    }),

  login: (body: AuthLoginBody) =>
    apiFetchParsed(BrowserAuthResponseSchema, apiRoutes.auth.login, {
      method: "POST",
      body,
    }),

  deviceSessionInfo: (sessionId: string) =>
    apiFetchParsed(
      DeviceSessionInfoSchema,
      apiRoutes.auth.deviceSessionInfo(sessionId),
    ),

  deviceSessionComplete: (sessionId: string, userCode: string) =>
    apiFetch(apiRoutes.auth.deviceSessionComplete(sessionId), {
      method: "POST",
      body: { user_code: userCode },
    }),

  deviceSessionDeny: (sessionId: string) =>
    apiFetch(apiRoutes.auth.deviceSessionDeny(sessionId), {
      method: "POST",
    }),
};
