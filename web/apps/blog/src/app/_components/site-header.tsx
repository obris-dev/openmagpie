import Link from "next/link";
import { ThemedLogo, ThemeToggle } from "@magpie/ui";
import { origins } from "@magpie/api-utils/site";

// The wordmark links home to the blog; the shared ThemedLogo swaps light/dark
// by color scheme. ThemeToggle is a client island.
export function SiteHeader() {
  return (
    <header className="border-b border-ink/10 dark:border-paper/10">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
        <Link href="/" className="flex items-center gap-2" aria-label="OpenMagpie Blog home">
          <ThemedLogo height={24} />
          <span className="text-sm font-medium text-ink-subtle">Blog</span>
        </Link>
        <div className="flex items-center gap-4">
          <a
            href={origins.marketing()}
            className="text-sm font-medium text-ink-muted transition-colors hover:text-signal-600 dark:text-ink-subtle dark:hover:text-signal-400"
          >
            openmagpie.ai
          </a>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
