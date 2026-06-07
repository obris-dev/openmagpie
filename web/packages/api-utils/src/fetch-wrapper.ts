/**
 * Tiny fetch wrapper with consistent error handling + credential semantics.
 *
 * - `credentials: "include"` so the auth_token cookie is sent on every API
 *   call. CLI usage routes through a different client entirely
 *   (cli/src/openmagpie/http.py uses Bearer).
 * - JSON in, JSON out. Throws `ApiError` (carrying status + parsed body)
 *   on non-2xx so callers can `try/catch` and surface form-level errors.
 * - For trusted shapes that drive auth state, use `apiFetchParsed`; it
 *   runs a runtime schema check so a malformed response throws instead
 *   of silently flowing into the store as a bad cast.
 */

import type { ZodType } from "zod";

import { buildApiUrl } from "./routes";

export interface ApiFetchOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;
  constructor(status: number, body: unknown, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export async function apiFetch<T = unknown>(
  path: string,
  options: ApiFetchOptions = {},
): Promise<T> {
  const { body, headers, ...rest } = options;
  const init: RequestInit = {
    credentials: "include",
    ...rest,
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...(headers as Record<string, string> | undefined),
    },
  };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
  }

  const response = await fetch(buildApiUrl(path), init);

  const text = await response.text();
  const parsed = text ? safeParseJson(text) : null;

  if (!response.ok) {
    const message =
      (parsed && typeof parsed === "object" && "detail" in parsed
        ? String((parsed as { detail: unknown }).detail)
        : null) ?? `Request failed with status ${response.status}`;
    throw new ApiError(response.status, parsed, message);
  }

  return parsed as T;
}

function safeParseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/**
 * Like `apiFetch`, but validates the response against a Zod schema and
 * returns the parsed value. Throws `ZodError` on mismatch; caller can
 * treat that as "server returned an unexpected shape" and surface a
 * generic failure rather than trusting the body.
 *
 * Use this for auth-shaped responses (`/me`, signup, login,
 * device-session info) where a bad shape silently flowing into the auth
 * store would be worse than an outright failure.
 */
export async function apiFetchParsed<T>(
  schema: ZodType<T>,
  path: string,
  options: ApiFetchOptions = {},
): Promise<T> {
  const raw = await apiFetch<unknown>(path, options);
  return schema.parse(raw);
}
