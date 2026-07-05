import { Logo } from "./logo";

/**
 * Wordmark in both light + dark variants; Tailwind's `dark:` picks which shows.
 * Pure CSS, no theme detection, so no hydration mismatch. Shared across the app,
 * marketing, and blog surfaces so the swap lives in one place.
 */
export interface ThemedLogoProps {
  height?: number;
}

export function ThemedLogo({ height = 32 }: ThemedLogoProps) {
  return (
    <>
      <Logo height={height} on="light" className="block dark:hidden" />
      <Logo height={height} on="dark" className="hidden dark:block" />
    </>
  );
}
