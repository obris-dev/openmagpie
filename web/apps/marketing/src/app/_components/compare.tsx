import { ConsoleLabel } from "./console-label";

const saas = [
  "Paywall, trial, or sales demo to start",
  "Limited to their sources and models",
  "Your data is locked into their platform",
  "Closed source, take it or leave it",
];

const magpie = [
  "Open source, Apache 2.0",
  "Self-hosted on your own box",
  "Bring your own LLM",
  "Natural-language matching, scored on meaning",
  "Auditable, every poll, judgement, and delivery is traceable",
  "Free to run, you only pay for your own hardware and model",
];

export function Comparison() {
  return (
    <section id="why" className="relative border-b border-ink/10 dark:border-paper/10">
      <div className="mx-auto max-w-6xl px-6 py-20 sm:py-28">
        <ConsoleLabel>The market vs OpenMagpie</ConsoleLabel>
        <h2 className="mt-5 max-w-3xl text-3xl font-bold tracking-tight text-balance sm:text-5xl">
          The open, self-hostable exception.
        </h2>
        <p className="mt-5 max-w-2xl text-lg leading-relaxed text-ink-muted dark:text-paper/70">
          Social listening is a crowded market of closed SaaS. Run OpenMagpie on
          your own box, with your own model, for the cost of the hardware.
        </p>

        <div className="mt-12 grid gap-5 lg:grid-cols-2">
          <div className="reveal rounded-2xl border border-ink/10 bg-paper-soft/50 p-7 dark:border-paper/10 dark:bg-ink-soft/40">
            <div className="font-mono text-xs uppercase tracking-[0.2em] text-ink-subtle dark:text-paper/55">
              Closed SaaS
            </div>
            <ul className="mt-5 space-y-3">
              {saas.map((x) => (
                <li
                  key={x}
                  className="flex gap-3 text-sm text-ink-muted dark:text-paper/70"
                >
                  <span aria-hidden className="mt-px select-none font-mono text-ink-subtle dark:text-paper/55">
                    ✗
                  </span>
                  {x}
                </li>
              ))}
            </ul>
          </div>

          <div className="reveal relative overflow-hidden rounded-2xl border border-signal/40 bg-paper p-7 shadow-lg shadow-signal/10 dark:bg-ink-soft">
            <div
              aria-hidden
              className="pointer-events-none absolute inset-0 dark:bg-[radial-gradient(ellipse_at_top_right,_rgba(0,183,195,0.12),_transparent_60%)]"
            />
            <div className="relative z-10">
              <div className="font-mono text-xs uppercase tracking-[0.2em] text-signal">
                OpenMagpie
              </div>
              <ul className="mt-5 space-y-3">
                {magpie.map((x) => (
                  <li key={x} className="flex gap-3 text-sm text-ink dark:text-paper">
                    <span aria-hidden className="mt-px select-none font-mono text-signal">
                      ✓
                    </span>
                    {x}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
