import { ConsoleLabel } from "./console-label";
import { Section, Heading, Lead } from "./section";

const steps = [
  {
    n: "01",
    title: "Curate your sources",
    body: "Point a feed at the subreddits, sites, and feeds where your people talk.",
  },
  {
    n: "02",
    title: "Describe relevance in plain language",
    body: "No keyword rules or boolean syntax. Just a sentence like “someone frustrated with manual social monitoring and asking for alternatives.”",
  },
  {
    n: "03",
    title: "Get the matches, not the firehose",
    body: "A model you run scores every new post. Matches land in a webhook or your logs while the thread is still live; everything else is dropped.",
  },
];

export function HowItWorks({ band }: { band?: boolean }) {
  return (
    <Section id="how" band={band}>
      <div className="grid gap-10 lg:grid-cols-[1fr_1.5fr] lg:gap-16">
        <div className="reveal">
          <ConsoleLabel>How it works</ConsoleLabel>
          <Heading>Describe what matters. Magpie watches the rest.</Heading>
          <Lead className="mt-5 max-w-sm">
            Three steps from a feed to the threads worth your reply.
          </Lead>
        </div>

        <ol>
          {steps.map((s, i) => {
            const last = i === steps.length - 1;
            return (
              <li
                key={s.n}
                className="reveal grid grid-cols-[2.5rem_1fr] gap-x-5 sm:grid-cols-[4rem_1fr] sm:gap-x-8"
              >
                <div className="flex flex-col items-center">
                  <span className="font-mono text-sm font-medium tabular-nums text-signal">
                    {s.n}
                  </span>
                  {!last && (
                    <span
                      aria-hidden
                      className="mt-3 w-px flex-1 bg-gradient-to-b from-ink/25 to-transparent dark:from-paper/25"
                    />
                  )}
                </div>
                <div className={last ? "" : "pb-12"}>
                  <h3 className="text-xl font-semibold">{s.title}</h3>
                  <p className="mt-2 leading-relaxed text-ink-muted dark:text-paper/70">
                    {s.body}
                  </p>
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </Section>
  );
}
