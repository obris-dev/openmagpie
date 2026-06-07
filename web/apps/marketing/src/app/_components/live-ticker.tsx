const matches = [
  { src: "r/SaaS", score: "0.94", text: "Tried 4 monitoring tools, still doing it by hand every morning" },
  { src: "Hacker News", score: "0.91", text: "Ask HN: tracking mentions without paying $400/mo?" },
  { src: "r/selfhosted", score: "0.89", text: "Anything open source for listening to subreddits + RSS?" },
  { src: "RSS | indie blog", score: "0.88", text: "Why we dropped our social-listening SaaS" },
  { src: "r/Entrepreneur", score: "0.92", text: "Where do you find people actually asking for what you build?" },
  { src: "Hacker News", score: "0.90", text: "Show HN: I self-host my own mention tracker" },
];

/**
 * The product's value, made visible: a console of matches streaming in. The
 * list is rendered twice so the CSS ticker can loop seamlessly; it pauses on
 * hover and freezes entirely under prefers-reduced-motion.
 */
export function LiveTicker() {
  return (
    <div className="ticker relative overflow-hidden rounded-2xl border border-ink/10 bg-paper-soft/70 shadow-xl shadow-ink/5 backdrop-blur-sm dark:border-paper/10 dark:bg-ink-soft/70 dark:shadow-black/30">
      <div className="flex items-center gap-2 border-b border-ink/10 px-4 py-3 dark:border-paper/10">
        <span className="pulse-dot size-2 rounded-full bg-signal" />
        <span className="font-mono text-xs uppercase tracking-[0.18em] text-signal">
          Live
        </span>
        <span className="font-mono text-xs text-ink-subtle dark:text-paper/55">
          matches | ai-tools-listener
        </span>
      </div>

      <div className="ticker-mask h-[22rem] overflow-hidden">
        <ul className="ticker-track">
          {[...matches, ...matches].map((m, i) => (
            <li
              key={i}
              className="border-b border-ink/5 px-4 py-3.5 dark:border-paper/5"
            >
              <div className="flex items-center justify-between font-mono text-xs">
                <span className="text-ink-subtle dark:text-paper/55">{m.src}</span>
                <span className="font-semibold text-signal">{m.score}</span>
              </div>
              <p className="mt-1.5 text-sm leading-snug text-ink/85 dark:text-paper/85">
                {m.text}
              </p>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
