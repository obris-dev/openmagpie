import { Logo } from "@magpie/ui";

/**
 * Wordmark in both variants, Tailwind's `dark:` picks which is visible.
 * Pure CSS, no theme detection, so no hydration mismatch. Mirrors the
 * product app's auth ThemedLogo.
 */
export function ThemedLogo({ height = 32 }: { height?: number }) {
  return (
    <>
      <Logo height={height} on="light" className="block dark:hidden" />
      <Logo height={height} on="dark" className="hidden dark:block" />
    </>
  );
}
