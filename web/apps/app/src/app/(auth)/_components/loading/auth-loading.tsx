import { ThemedLogo } from "@magpie/ui";

/**
 * Suspense fallback for the auth pages (home / login / signup). They're client
 * components that read useSearchParams, so each page wraps its content in a
 * Suspense boundary; this is the shared fallback.
 *
 * It mirrors the pages' centered-card shell (and renders inside AuthLayout's
 * gradient/mascot chrome), so the prerendered/initial paint matches the real
 * page instead of flashing blank. Reuses ThemedLogo + the same card classes so
 * it stays in sync; if the card shell ever diverges, extract a shared shell
 * component used by both the pages and this fallback.
 */
export function AuthLoading() {
  return (
    <div className="flex min-h-dvh flex-1 flex-col justify-center py-12 sm:px-6 lg:px-8">
      <div className="sm:mx-auto sm:w-full sm:max-w-md">
        <div className="flex justify-center">
          <ThemedLogo height={52} />
        </div>
      </div>

      <div className="mt-10 sm:mx-auto sm:w-full sm:max-w-[480px]">
        <div className="bg-paper px-6 py-12 shadow sm:rounded-lg sm:px-12 dark:bg-ink-soft">
          <div className="animate-pulse space-y-6" aria-hidden>
            <div className="mx-auto h-4 w-1/2 rounded bg-ink/10 dark:bg-paper/10" />
            <div className="h-10 rounded bg-ink/10 dark:bg-paper/10" />
            <div className="h-10 rounded bg-ink/10 dark:bg-paper/10" />
            <div className="h-10 rounded bg-ink/10 dark:bg-paper/10" />
          </div>
          <span className="sr-only">Loading…</span>
        </div>
      </div>
    </div>
  );
}
