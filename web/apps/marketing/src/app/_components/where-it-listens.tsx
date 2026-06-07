import { ConsoleLabel } from "./console-label";
import { Section, Heading } from "./section";

const lanes = [
  {
    tag: "Today",
    live: true,
    title: "Public discussion",
    body: "Reddit, Hacker News, and any RSS or Atom feed, news, blogs, and forums that publish one.",
    sources: ["Reddit", "Hacker News", "RSS / Atom"],
  },
  {
    tag: "Next",
    live: false,
    title: "Communities you're in",
    body: "Slack workspaces and LinkedIn you already belong to. No admin or app install required.",
    sources: ["Slack", "LinkedIn"],
  },
];

export function WhereItListens({ band }: { band?: boolean }) {
  return (
    <Section id="where" band={band}>
      <ConsoleLabel>Where it listens</ConsoleLabel>
      <Heading className="max-w-2xl">
        Wherever communities are having the conversation.
      </Heading>

      <div className="mt-14 grid gap-12 sm:grid-cols-2 sm:gap-0 sm:divide-x sm:divide-ink/10 sm:dark:divide-paper/10">
        {lanes.map((l, i) => (
          <div key={l.title} className={`reveal ${i === 0 ? "sm:pr-12" : "sm:pl-12"}`}>
            <div className="flex items-center gap-2.5">
              <span
                aria-hidden
                className={
                  l.live
                    ? "pulse-dot size-2 rounded-full bg-signal"
                    : "size-2 rounded-full border border-ink/30 dark:border-paper/30"
                }
              />
              <span className="font-mono text-xs uppercase tracking-[0.22em] text-signal">
                {l.tag}
              </span>
            </div>
            <h3 className="mt-4 text-xl font-semibold">{l.title}</h3>
            <p className="mt-2 leading-relaxed text-ink-muted dark:text-paper/70">
              {l.body}
            </p>
            <ul className="mt-5 flex flex-wrap gap-2">
              {l.sources.map((src) => (
                <li
                  key={src}
                  className="rounded-full border border-ink/10 px-3 py-1 font-mono text-xs text-ink-muted dark:border-paper/10 dark:text-paper/70"
                >
                  {src}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Section>
  );
}
