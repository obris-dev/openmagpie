"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button, Input, Label, PasswordInput, ThemedLogo } from "@magpie/ui";
import {
  ApiError,
  authActions,
  safeNext,
  webRoutes,
  withNext,
} from "@magpie/api-utils";
import { useAuthStore } from "@magpie/auth";
import { AuthFooter } from "@/app/(auth)/_components/auth-footer";

const REMEMBERED_EMAIL_KEY = "magpie_remembered_email";

export function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const setUser = useAuthStore((s) => s.setUser);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [rememberMe, setRememberMe] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const remembered = localStorage.getItem(REMEMBERED_EMAIL_KEY);
    if (remembered) {
      setEmail(remembered);
      setRememberMe(true);
    }
  }, []);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    setError("");

    try {
      const result = await authActions.login({ email, password });

      if (rememberMe) {
        localStorage.setItem(REMEMBERED_EMAIL_KEY, email);
      } else {
        localStorage.removeItem(REMEMBERED_EMAIL_KEY);
      }

      setUser(result.user);
      router.push(safeNext(searchParams.get("next")));
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "Unable to sign in. Please check your credentials and try again."
          : err instanceof ApiError
            ? err.message
            : "Something went wrong. Please try again.",
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-dvh flex-1 flex-col justify-center py-12 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md">
        <div className="flex justify-center">
          <ThemedLogo height={52} />
        </div>
        <h2 className="mt-6 text-center text-2xl leading-9 font-bold tracking-tight text-ink dark:text-paper">
          Sign in to your account
        </h2>
      </div>

      <div className="mt-10 sm:mx-auto sm:w-full sm:max-w-[480px]">
        <div className="bg-paper px-6 py-12 shadow sm:rounded-lg sm:px-12 dark:bg-ink-soft">
          {error && (
            <div
              role="alert"
              className="mb-6 rounded-md bg-red-50 p-4 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
            >
              {error}
            </div>
          )}

          <form className="space-y-6" onSubmit={handleSubmit} noValidate>
            <div>
              <Label htmlFor="email">Email address</Label>
              <div className="mt-2">
                <Input
                  id="email"
                  name="email"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
            </div>

            <div>
              <Label htmlFor="password">Password</Label>
              <div className="mt-2">
                <PasswordInput
                  id="password"
                  name="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
            </div>

            <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-ink/80 select-none dark:text-paper/80">
              <input
                name="remember-me"
                type="checkbox"
                checked={rememberMe}
                onChange={(e) => setRememberMe(e.target.checked)}
                className="size-4 rounded border-ink/20 text-signal focus:ring-signal dark:border-paper/20 dark:bg-ink-soft"
              />
              Remember me
            </label>

            <div>
              <Button type="submit" fullWidth loading={isSubmitting}>
                {isSubmitting ? "Signing in…" : "Sign in"}
              </Button>
            </div>
          </form>

          <div className="mt-6">
            <div className="relative">
              <div className="absolute inset-0 flex items-center">
                <div className="w-full border-t border-ink/15 dark:border-paper/15" />
              </div>
              <div className="relative flex justify-center text-sm leading-6">
                <span className="bg-paper px-6 text-ink/60 dark:bg-ink-soft dark:text-paper/60">
                  Don&apos;t have an account?
                </span>
              </div>
            </div>

            <div className="mt-6">
              <Button
                variant="secondary"
                fullWidth
                onClick={() =>
                  router.push(withNext(webRoutes.signup, searchParams.get("next")))
                }
              >
                Create account
              </Button>
            </div>
          </div>
        </div>
      </div>

      <AuthFooter />
    </div>
  );
}
