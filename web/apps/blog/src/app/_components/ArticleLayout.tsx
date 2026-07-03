import Link from "next/link";
import type { ReactNode } from "react";
import { Prose } from "./Prose";

function formatDate(dateString: string): string {
  return new Date(dateString + "T00:00:00").toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

export function ArticleLayout({
  title,
  date,
  author,
  children,
}: {
  title: string;
  date: string;
  author?: string;
  children: ReactNode;
}) {
  return (
    <article className="mx-auto max-w-3xl px-6 py-16">
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

      <Prose>{children}</Prose>
    </article>
  );
}
