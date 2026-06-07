import { ConsoleLabel } from "./console-label";

export function Problem() {
  return (
    <section className="relative border-b border-ink/10 dark:border-paper/10">
      <div className="mx-auto max-w-6xl px-6 py-20 sm:py-28">
        <div className="reveal grid gap-8 md:grid-cols-[1fr_1.3fr] md:gap-16">
          <div>
            <ConsoleLabel>The problem</ConsoleLabel>
            <h2 className="mt-5 text-3xl font-bold tracking-tight text-balance sm:text-5xl">
              You&apos;re already doing this by hand.
            </h2>
          </div>
          <p className="text-lg leading-relaxed text-ink-muted dark:text-paper/70">
            Every day you check Reddit, Hacker News, and a handful of feeds for
            the same things: someone hitting a problem your product solves, a
            question in your wheelhouse, a mention of you or a competitor. The
            best threads are gone by the time you find them, and a late reply is
            just noise.
          </p>
        </div>
      </div>
    </section>
  );
}
