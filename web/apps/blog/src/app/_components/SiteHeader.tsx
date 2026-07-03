import Link from "next/link";
import { Logo, ThemeToggle } from "@magpie/ui";
import { origins } from "@magpie/api-utils/site";

// The wordmark links home to the blog; a themed pair swaps by color scheme
// (matches marketing's themed-logo). ThemeToggle is a client island.
export function SiteHeader() {
  return (
    <header className="border-b border-ink/10 dark:border-paper/10">
      <div className="mx-auto flex max-w-3xl items-center justify-between px-6 py-4">
        <Link href="/" className="flex items-center gap-2" aria-label="OpenMagpie Blog home">
          <Logo height={24} on="light" className="block dark:hidden" />
          <Logo height={24} on="dark" className="hidden dark:block" />
          <span className="text-sm font-medium text-ink-subtle">Blog</span>
        </Link>
        <div className="flex items-center gap-4">
          <a
            href={origins.marketing}
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
