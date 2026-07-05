import { ThemedLogo, ThemeToggle } from "@magpie/ui";
import { GithubLink } from "./github-link";
import { MobileMenu } from "./mobile-menu";
import { links } from "../_lib/constants";

export function Nav() {
  return (
    <header className="fixed inset-x-0 top-0 z-50 border-b border-ink/10 bg-paper/70 backdrop-blur-xl dark:border-paper/10 dark:bg-ink/70">
      <div
        aria-hidden
        className="h-px w-full bg-gradient-to-r from-transparent via-signal/60 to-transparent"
      />
      <nav className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3.5">
        <a href="#top" aria-label="OpenMagpie home" className="shrink-0">
          <ThemedLogo height={26} />
        </a>
        <div className="hidden items-center gap-7 text-sm text-ink-muted min-[880px]:flex dark:text-paper/70">
          <a href="#how" className="hover:text-ink dark:hover:text-paper">
            How it works
          </a>
          <a href="#where" className="hover:text-ink dark:hover:text-paper">
            Where it listens
          </a>
          <a href="#why" className="hover:text-ink dark:hover:text-paper">
            Why self-host
          </a>
        </div>
        <div className="flex items-center gap-2 sm:gap-3">
          <a
            href={links.blog}
            className="hidden items-center rounded-md px-2.5 py-1.5 text-sm text-ink-muted hover:text-ink min-[880px]:inline-flex dark:text-paper/70 dark:hover:text-paper"
          >
            Blog
          </a>
          <GithubLink
            aria-label="OpenMagpie on GitHub"
            className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm text-ink-muted hover:text-ink dark:text-paper/70 dark:hover:text-paper"
          >
            <span className="hidden min-[880px]:inline">GitHub</span>
          </GithubLink>
          <a
            href="#waitlist"
            className="hidden items-center rounded-md bg-signal px-4 py-2 text-sm font-medium text-paper transition-colors hover:bg-signal-600 min-[880px]:inline-flex dark:hover:bg-signal-700"
          >
            Join the waitlist
          </a>
          <ThemeToggle />
          <MobileMenu />
        </div>
      </nav>
    </header>
  );
}
