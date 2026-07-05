"use client";

import { useState } from "react";
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

const MIN_PASSWORD = 8;

export function SignupForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next");
  const setUser = useAuthStore((s) => s.setUser);

  const [email, setEmail] = useState("");
  const [password1, setPassword1] = useState("");
  const [password2, setPassword2] = useState("");
  const [passwordError, setPasswordError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    setError("");
    setPasswordError("");

    if (password1 !== password2) {
      setPasswordError("Passwords do not match.");
      setIsSubmitting(false);
      return;
    }

    try {
      const result = await authActions.signup({ email, password: password1 });
      setUser(result.user);
      router.push(safeNext(next));
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Unable to create your account. Please try again.",
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
          Create your account
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
              <Label htmlFor="password1">Password</Label>
              <div className="mt-2">
                <PasswordInput
                  id="password1"
                  name="password1"
                  autoComplete="new-password"
                  required
                  minLength={MIN_PASSWORD}
                  value={password1}
                  onChange={(e) => setPassword1(e.target.value)}
                />
              </div>
            </div>

            <div>
              <Label htmlFor="password2">Confirm password</Label>
              <div className="mt-2">
                <PasswordInput
                  id="password2"
                  name="password2"
                  autoComplete="new-password"
                  required
                  minLength={MIN_PASSWORD}
                  invalid={Boolean(passwordError)}
                  value={password2}
                  onChange={(e) => setPassword2(e.target.value)}
                />
              </div>
              {passwordError && (
                <p className="mt-2 text-sm text-red-600 dark:text-red-400">
                  {passwordError}
                </p>
              )}
            </div>

            <div>
              <Button type="submit" fullWidth loading={isSubmitting}>
                {isSubmitting ? "Creating account…" : "Create account"}
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
                  Already have an account?
                </span>
              </div>
            </div>

            <div className="mt-6">
              <Button
                variant="secondary"
                fullWidth
                onClick={() => router.push(withNext(webRoutes.login, next))}
              >
                Sign in instead
              </Button>
            </div>
          </div>
        </div>
      </div>

      <AuthFooter />
    </div>
  );
}
