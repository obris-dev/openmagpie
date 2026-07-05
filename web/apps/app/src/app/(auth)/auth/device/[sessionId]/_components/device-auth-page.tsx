"use client";

import { useEffect, useState } from "react";
import { Button, Emblem, Input, Label, ThemedLogo } from "@magpie/ui";
import {
  ApiError,
  authActions,
  type DeviceSessionInfo,
} from "@magpie/api-utils";
import { useRequireAuth } from "@magpie/auth";
import { AuthFooter } from "@/app/(auth)/_components/auth-footer";

const PHASE = {
  CONFIRM: "confirm",
  COMPLETING: "completing",
  COMPLETE: "complete",
  DENYING: "denying",
  DENIED: "denied",
} as const;

type Phase = (typeof PHASE)[keyof typeof PHASE];

function Field({ label, value }: { label: string; value: string | null }) {
  if (!value) return null;
  return (
    <div className="flex items-baseline justify-between gap-4 text-xs">
      <span className="shrink-0 text-ink/60 dark:text-paper/60">{label}</span>
      <code className="truncate text-right font-mono text-ink dark:text-paper">
        {value}
      </code>
    </div>
  );
}

/** True when the web app itself is running on localhost. Used to
 * suppress the "From" row entirely in dev, since every IP we'd show
 * is a local-networking artifact (Docker bridge, loopback, etc.). */
function isLocalDevHost(): boolean {
  if (typeof window === "undefined") return false;
  return (
    window.location.hostname === "localhost" ||
    window.location.hostname === "127.0.0.1"
  );
}

export function DeviceAuthPage({ sessionId }: { sessionId: string }) {
  const { user, loading, error: authError } = useRequireAuth();
  const [phase, setPhase] = useState<Phase>(PHASE.CONFIRM);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<DeviceSessionInfo | null>(null);
  const [infoError, setInfoError] = useState<string | null>(null);
  const [code, setCode] = useState("");

  // Once the user is known to be signed in, fetch the session metadata
  // (initiator client info + IP) so the page can render them next to
  // the code input. The `user_code` itself is deliberately NOT exposed
  // here, the user has to read it from their own terminal, which is
  // the defense against being phished onto an attacker's session URL.
  useEffect(() => {
    if (loading || user === null) return;
    let cancelled = false;
    async function load() {
      try {
        const s = await authActions.deviceSessionInfo(sessionId);
        if (!cancelled) setInfo(s);
      } catch (err) {
        if (cancelled) return;
        setInfoError(
          err instanceof ApiError
            ? "This session has expired or doesn't exist."
            : "Couldn't load session details.",
        );
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [loading, user, sessionId]);

  async function authorize(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPhase(PHASE.COMPLETING);
    try {
      await authActions.deviceSessionComplete(sessionId, code);
      setPhase(PHASE.COMPLETE);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.status === 403
            ? "That code doesn't match the one in your terminal."
            : err.status === 404
              ? "This session has expired. Run `magpie auth login` again."
              : err.message
          : "Something went wrong. Please try again.",
      );
      setPhase(PHASE.CONFIRM);
    }
  }

  async function deny() {
    setError(null);
    setPhase(PHASE.DENYING);
    try {
      await authActions.deviceSessionDeny(sessionId);
    } catch {
      // swallow, local state below is the user-visible signal
    }
    setPhase(PHASE.DENIED);
  }

  return (
    <div className="flex min-h-dvh flex-1 flex-col justify-center py-12 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md">
        <div className="flex justify-center">
          <ThemedLogo height={52} />
        </div>
        <h2 className="mt-6 text-center text-2xl leading-9 font-bold tracking-tight text-ink dark:text-paper">
          {phase === PHASE.COMPLETE
            ? "You're all set"
            : phase === PHASE.DENIED
              ? "Authorization canceled"
              : "Authorize this CLI?"}
        </h2>
      </div>

      <div className="mt-10 sm:mx-auto sm:w-full sm:max-w-[480px]">
        <div className="bg-paper px-6 py-12 shadow sm:rounded-lg sm:px-12 dark:bg-ink-soft">
          {authError && user === null ? (
            // useRequireAuth couldn't determine auth state (network /
            // CORS / 5xx). Don't redirect, don't spin forever; surface
            // a retry so a flaky connection isn't a dead-end.
            <div className="space-y-5 text-center">
              <p className="text-sm text-ink/70 dark:text-paper/70">
                Couldn't reach the server to verify your session.
              </p>
              <Button
                variant="secondary"
                onClick={() => window.location.reload()}
              >
                Try again
              </Button>
            </div>
          ) : loading || user === null ? (
            <div className="py-8 text-center text-sm text-ink/60 dark:text-paper/60">
              Checking your session…
            </div>
          ) : phase === PHASE.COMPLETE ? (
            <div className="space-y-5 text-center">
              <div className="flex justify-center">
                <Emblem size={64} />
              </div>
              <p className="text-sm text-ink/70 dark:text-paper/70">
                Head back to your terminal. The CLI has your credentials.
                You can close this tab.
              </p>
            </div>
          ) : phase === PHASE.DENIED ? (
            <div className="space-y-5 text-center">
              <p className="text-sm text-ink/70 dark:text-paper/70">
                The CLI session was not granted access. You can close this tab.
              </p>
            </div>
          ) : infoError ? (
            <div className="space-y-5 text-center">
              <p className="text-sm text-red-700 dark:text-red-300">
                {infoError}
              </p>
            </div>
          ) : (
            <form className="space-y-6" onSubmit={authorize} noValidate>
              <p className="text-sm text-ink/70 dark:text-paper/70">
                A terminal session is asking to sign in as{" "}
                <span className="font-medium text-ink dark:text-paper">
                  {user.email}
                </span>
                . Only continue if you opened this page from your own{" "}
                <code className="font-mono text-xs text-ink dark:text-paper">
                  magpie auth login
                </code>{" "}
                command.
              </p>

              {info && (
                <div className="space-y-1.5 rounded-md bg-paper-soft px-4 py-3 ring-1 ring-inset ring-ink/10 dark:bg-ink dark:ring-paper/10">
                  <Field
                    label="CLI"
                    value={
                      info.initiator?.name && info.initiator?.version
                        ? `${info.initiator.name} ${info.initiator.version}`
                        : null
                    }
                  />
                  <Field
                    label="Host"
                    value={info.initiator?.hostname || null}
                  />
                  {!isLocalDevHost() && info.initiator_ip && (
                    <Field label="From" value={info.initiator_ip} />
                  )}
                </div>
              )}

              <div>
                <Label htmlFor="user-code">
                  Verification code from your terminal
                </Label>
                <div className="mt-2">
                  <Input
                    id="user-code"
                    name="user-code"
                    type="text"
                    autoComplete="off"
                    autoCapitalize="characters"
                    autoCorrect="off"
                    spellCheck={false}
                    required
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    placeholder="ABCD-EFGH"
                    className="font-mono tracking-widest"
                  />
                </div>
              </div>

              {error && (
                <div
                  role="alert"
                  className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
                >
                  {error}
                </div>
              )}

              <div className="flex gap-3">
                <Button
                  type="button"
                  variant="secondary"
                  fullWidth
                  loading={phase === PHASE.DENYING}
                  disabled={
                    phase === PHASE.COMPLETING || phase === PHASE.DENYING
                  }
                  onClick={deny}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  fullWidth
                  loading={phase === PHASE.COMPLETING}
                  disabled={
                    phase === PHASE.COMPLETING ||
                    phase === PHASE.DENYING ||
                    code.trim() === ""
                  }
                >
                  Authorize
                </Button>
              </div>
            </form>
          )}
        </div>
      </div>

      <AuthFooter />
    </div>
  );
}
