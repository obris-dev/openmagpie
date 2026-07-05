import Link from "next/link";
import { getAllPosts } from "@/app/_lib/posts";
import { formatDate } from "@/app/_lib/format-date";
import { BLOG_DESCRIPTION } from "@/app/_lib/blog-meta";

export default function BlogIndex() {
  const posts = getAllPosts();

  return (
    <div className="mx-auto max-w-3xl px-6 py-16">
      <header className="mb-16">
        <h1 className="font-sans text-4xl font-bold tracking-tight text-ink sm:text-5xl dark:text-paper">
          Blog
        </h1>
        <p className="mt-4 text-lg text-ink-muted dark:text-ink-subtle">
          {BLOG_DESCRIPTION}
        </p>
      </header>

      <div className="flex flex-col gap-12">
        {posts.map((post) => (
          <article key={post.slug} className="group">
            <Link href={post.href} className="block">
              <div className="flex items-center gap-2 text-sm text-ink-subtle">
                <time dateTime={post.date}>{formatDate(post.date)}</time>
                {post.author && (
                  <>
                    <span aria-hidden="true">|</span>
                    <span>{post.author}</span>
                  </>
                )}
              </div>
              <h2 className="mt-1 font-sans text-xl font-semibold text-ink group-hover:text-signal-600 dark:text-paper dark:group-hover:text-signal-400">
                {post.title}
              </h2>
              {post.description && (
                <p className="mt-2 text-ink-muted dark:text-ink-subtle">
                  {post.description}
                </p>
              )}
              <span className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-signal-600 dark:text-signal-400">
                Read post
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
                  <path d="M5 12h14M12 5l7 7-7 7" />
                </svg>
              </span>
            </Link>
          </article>
        ))}
      </div>
    </div>
  );
}
