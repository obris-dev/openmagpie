import { links } from "../_lib/constants";
import { ExternalLink } from "./external-link";
import { ThemedLogo } from "@magpie/ui";

export function Footer() {
  return (
    <footer className="border-t border-ink/10 dark:border-paper/10">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 py-10 text-sm text-ink-subtle dark:text-paper/55 sm:flex-row">
        <div className="flex items-center gap-3">
          <ThemedLogo height={22} />
          <span>© {new Date().getFullYear()} OpenMagpie</span>
        </div>
        <div className="flex items-center gap-6">
          {/* First-party destination: same-tab, no external glyph (matches nav). */}
          <a href={links.blog} className="underline-offset-4 hover:underline">
            Blog
          </a>
          <ExternalLink href={links.docs}>Docs</ExternalLink>
          <ExternalLink href={links.github}>GitHub</ExternalLink>
        </div>
      </div>
    </footer>
  );
}
