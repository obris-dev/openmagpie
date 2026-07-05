import Link from "next/link";
import { siteMeta } from "@magpie/api-utils/site";

/**
 * Footer for the auth surface. Renders below the card. Minimal links
 * for now; replace `#` hrefs with real routes when we have them.
 */
export function AuthFooter() {
  return (
    <footer className="mx-auto mt-10 max-w-md text-center text-xs leading-6 text-ink/50 dark:text-paper/40">
      <div className="space-x-3">
        <Link
          href="#"
          className="hover:text-ink dark:hover:text-paper"
        >
          Terms
        </Link>
        <span aria-hidden>|</span>
        <Link
          href="#"
          className="hover:text-ink dark:hover:text-paper"
        >
          Privacy
        </Link>
        <span aria-hidden>|</span>
        <a
          href={siteMeta.repoUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="hover:text-ink dark:hover:text-paper"
        >
          GitHub
        </a>
      </div>
    </footer>
  );
}
