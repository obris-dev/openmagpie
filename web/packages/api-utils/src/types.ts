import { AuthUserSchema } from "@magpie/schema";
import { z } from "zod";

/**
 * Runtime schemas for the auth surfaces. Used by `apiFetchParsed` so a
 * malformed server response throws (with a useful `ZodError`) instead
 * of silently flowing into the auth store as a bad cast.
 *
 * `AuthUser` is the generated contract schema from `@magpie/schema` (one
 * source of truth shared with the server + CLI). The browser-specific auth
 * shapes below are defined here; TS types are inferred from the schemas.
 */

export interface AuthSignupBody {
  email: string;
  password: string;
}

export interface AuthLoginBody {
  email: string;
  password: string;
}

/**
 * Browser-facing signup / login response. Tokens never appear in the
 * body, they ride in the `auth_token` HttpOnly cookie set on the same
 * response. The CLI uses a different path entirely (device-flow).
 */
export const BrowserAuthResponseSchema = z.object({
  user: AuthUserSchema,
});
export type BrowserAuthResponse = z.infer<typeof BrowserAuthResponseSchema>;

export const DeviceSessionCreateResponseSchema = z.object({
  session_id: z.string(),
  /** Browser URL the CLI prints / opens. */
  authorize_url: z.string(),
});
export type DeviceSessionCreateResponse = z.infer<
  typeof DeviceSessionCreateResponseSchema
>;

/**
 * Audit metadata shown on the authorize page. The `user_code` is
 * deliberately NOT in here, defending against phishing requires the
 * user reads it from their own terminal, not the URL they clicked.
 */
export const DeviceSessionInitiatorSchema = z.object({
  name: z.string(),
  version: z.string(),
  hostname: z.string(),
});
export type DeviceSessionInitiator = z.infer<typeof DeviceSessionInitiatorSchema>;

export const DeviceSessionInfoSchema = z.object({
  status: z.string(),
  created_at: z.string().nullable(),
  initiator_ip: z.string().nullable(),
  initiator: DeviceSessionInitiatorSchema.nullable(),
});
export type DeviceSessionInfo = z.infer<typeof DeviceSessionInfoSchema>;

/**
 * Device-session poll response. The completed branch carries the OAuth
 * token pair for the CLI to pick up, this is the ONLY surface that
 * exposes raw tokens (and it's CLI-only; the browser never reads this).
 */
export const DeviceSessionPollResponseSchema = z.discriminatedUnion("status", [
  z.object({ status: z.literal("pending") }),
  z.object({
    status: z.literal("completed"),
    access_token: z.string(),
    refresh_token: z.string(),
    expires_in: z.number(),
    token_type: z.literal("Bearer"),
    user: AuthUserSchema,
  }),
  z.object({ status: z.literal("expired") }),
]);
export type DeviceSessionPollResponse = z.infer<
  typeof DeviceSessionPollResponseSchema
>;
