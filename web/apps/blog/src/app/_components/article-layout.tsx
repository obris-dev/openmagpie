import Link from "next/link";
import type { ReactNode } from "react";
import type { Section } from "@/app/_lib/posts";
import { Prose } from "./prose";
import { TableOfContents } from "./table-of-contents";
import { MobileToc } from "./mobile-toc";
import { formatDate } from "@/app/_lib/format-date";

export function ArticleLayout({
  title,
  date,
  author,
  sections,
  children,
}: {
  title: string;
  date: string;
  author?: string;
  sections?: Section[];
  children: ReactNode;
}) {
  const hasToc = Boolean(sections && sections.length > 0);

  return (
    <div className="mx-auto grid max-w-6xl grid-cols-1 gap-x-12 px-6 py-16 lg:grid-cols-[minmax(0,1fr)_15rem]">
      <article className="min-w-0 max-w-3xl">
        <header className="mb-12">
          <Link
            href="/"
            className="mb-8 inline-flex items-center gap-1 text-sm text-ink-subtle transition-colors hover:text-ink dark:text-ink-subtle dark:hover:text-paper"
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M19 12H5M12 19l-7-7 7-7" />
            </svg>
            All posts
          </Link>

          <time
            dateTime={date}
            className="block text-sm text-ink-subtle dark:text-ink-subtle"
          >
            {formatDate(date)}
          </time>

          <h1 className="mt-2 font-sans text-3xl font-bold tracking-tight text-ink sm:text-4xl dark:text-paper">
            {title}
          </h1>

          {author && (
            <p className="mt-6 text-sm text-ink-muted dark:text-ink-subtle">
              by {author}
            </p>
          )}
        </header>

        {/* Mobile/tablet: TOC in a slide-in drawer (the sticky sidebar below is
            lg-only, where there's room for it). */}
        {hasToc && <MobileToc sections={sections!} />}

        <Prose>{children}</Prose>
      </article>

      {hasToc && (
        <aside className="hidden lg:block">
          <TableOfContents sections={sections!} />
        </aside>
      )}
    </div>
  );
}
