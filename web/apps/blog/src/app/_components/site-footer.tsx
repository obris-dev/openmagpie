import { origins, siteMeta, BLOG_BASE_PATH } from "@magpie/api-utils/site";

export function SiteFooter() {
  return (
    <footer className="border-t border-ink/10 dark:border-paper/10">
      <div className="mx-auto flex max-w-6xl flex-col gap-2 px-6 py-8 text-sm text-ink-subtle sm:flex-row sm:items-center sm:justify-between">
        <p>&copy; {new Date().getFullYear()} OpenMagpie</p>
        <nav className="flex items-center gap-4">
          <a
            href={origins.marketing()}
            className="transition-colors hover:text-signal-600 dark:hover:text-signal-400"
          >
            Product
          </a>
          <a
            href={siteMeta.repoUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="transition-colors hover:text-signal-600 dark:hover:text-signal-400"
          >
            GitHub
          </a>
          {/* Same-origin path under the /blog basePath: correct in dev and prod
              without relying on NEXT_PUBLIC_SITE_URL, and a full-page nav (right
              for an XML endpoint, not client routing). */}
          <a
            href={`${BLOG_BASE_PATH}/feed.xml`}
            className="transition-colors hover:text-signal-600 dark:hover:text-signal-400"
          >
            RSS
          </a>
        </nav>
      </div>
    </footer>
  );
}
