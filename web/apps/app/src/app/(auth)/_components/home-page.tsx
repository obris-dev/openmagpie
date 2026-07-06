"use client";

import { useRouter } from "next/navigation";
import { Button, Emblem, ThemedLogo } from "@magpie/ui";
import { PH_NO_CAPTURE_CLASS } from "@magpie/ui/analytics";
import { webRoutes } from "@magpie/api-utils";
import { useRequireAuth } from "@magpie/auth";
import { AuthFooter } from "./auth-footer";

/**
 * Root page for authenticated users. v0 doesn't have a real dashboard
 * yet, so we show a "you're signed in" confirmation card matching the
 * device-authorize completion shape. Unauthenticated visitors get
 * bounced to /login by useRequireAuth.
 *
 * When the first product surface lands (listener list), replace this
 * page with that.
 */
export function HomePage() {
  const { user, loading } = useRequireAuth();
  const router = useRouter();

  if (loading || user === null) {
    return (
      <div className="flex min-h-dvh items-center justify-center text-sm text-ink/60 dark:text-paper/60">
        Checking your session…
      </div>
    );
  }

  return (
    <div className="flex min-h-dvh flex-1 flex-col justify-center py-12 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md">
        <div className="flex justify-center">
          <ThemedLogo height={52} />
        </div>
        <h2 className="mt-6 text-center text-2xl leading-9 font-bold tracking-tight text-ink dark:text-paper">
          You&apos;re signed in
        </h2>
      </div>

      <div className="mt-10 sm:mx-auto sm:w-full sm:max-w-[480px]">
        <div className="bg-paper px-6 py-12 shadow sm:rounded-lg sm:px-12 dark:bg-ink-soft">
          <div className="space-y-6 text-center">
            <div className="flex justify-center">
              <Emblem size={64} />
            </div>

            <p className="text-sm text-ink/70 dark:text-paper/70">
              Logged in as{" "}
              <span
                className={`${PH_NO_CAPTURE_CLASS} font-medium text-ink dark:text-paper`}
              >
                {user.email}
              </span>
              .
            </p>

            <p className="text-xs text-ink/50 dark:text-paper/50">
              Interactive UI pending. Use the magpie CLI for now. Run{" "}
              <code className="font-mono text-ink/70 dark:text-paper/70">
                magpie --help
              </code>{" "}
              to get started.
            </p>

            <Button
              variant="secondary"
              fullWidth
              onClick={() => router.push(webRoutes.logout)}
            >
              Sign out
            </Button>
          </div>
        </div>
      </div>

      <AuthFooter />
    </div>
  );
}
