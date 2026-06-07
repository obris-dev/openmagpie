import { Mascot } from "@magpie/ui";
import { links } from "./constants";
import { GithubIcon } from "./icons";
import { WaitlistForm } from "./waitlist-form";
import { LiveTicker } from "./live-ticker";

const badges = ["Apache 2.0", "Self-hostable", "Bring your own LLM"];

export function Hero() {
  return (
    <section
      id="top"
      className="relative overflow-hidden border-b border-ink/10 dark:border-paper/10"
    >
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top,_rgba(0,183,195,0.10),_transparent_55%)] dark:bg-[radial-gradient(ellipse_at_top,_rgba(125,249,255,0.07),_transparent_50%)]"
      />
      {/* Signal-grid floor. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-[0.35] [background-image:linear-gradient(rgba(0,183,195,0.07)_1px,transparent_1px),linear-gradient(90deg,rgba(0,183,195,0.07)_1px,transparent_1px)] [background-size:48px_48px] [mask-image:radial-gradient(ellipse_at_top,black,transparent_75%)]"
      />

      <div className="relative z-10 mx-auto max-w-6xl px-6 pt-32 pb-16 sm:pt-40">
        <div className="load-in flex flex-wrap items-center gap-2">
          {badges.map((b) => (
            <span
              key={b}
              className="rounded-full border border-ink/10 bg-paper-soft px-3 py-1 text-xs font-medium text-ink-muted dark:border-paper/10 dark:bg-ink-soft dark:text-paper/70"
            >
              {b}
            </span>
          ))}
        </div>

        <h1
          className="load-in mt-7 max-w-4xl text-5xl font-bold tracking-tight text-balance sm:text-7xl"
          style={{ animationDelay: "80ms" }}
        >
          Find the conversations that matter.{" "}
          <span className="text-signal [text-shadow:0_0_22px_rgba(0,183,195,0.18)]">
            Join in while they&apos;re happening.
          </span>
        </h1>

        <div className="mt-10 grid gap-10 lg:grid-cols-2 lg:gap-12">
          <div>
            <p
              className="load-in max-w-xl text-lg leading-relaxed text-ink-muted dark:text-paper/70"
              style={{ animationDelay: "160ms" }}
            >
              OpenMagpie is an open-source, self-hostable social listening tool
              for Reddit, Hacker News, and your feeds. Describe what you care
              about in natural language, and it surfaces the threads worth your
              reply, so you spend your time engaging, not searching.
            </p>
            <div
              className="load-in mt-8 max-w-lg"
              style={{ animationDelay: "240ms" }}
            >
              <div className="mb-3">
                <p className="text-sm font-semibold text-ink dark:text-paper">
                  Don&apos;t want to run it yourself?
                </p>
                <p className="text-sm text-ink-muted dark:text-paper/70">
                  Sign up for early access to our hosted version.
                </p>
              </div>
              <WaitlistForm idPrefix="hero" />
              <p className="mt-3 text-sm text-ink-subtle dark:text-paper/55">
                One email, at launch. Or self-host free, CLI today with a web UI
                on the way.{" "}
                <a
                  href={links.github}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="font-medium text-ink-muted underline-offset-4 hover:text-ink hover:underline dark:text-paper/70 dark:hover:text-paper"
                >
                  <GithubIcon className="mr-1 inline-block size-3.5 align-[-0.15em]" />
                  Self-host it free
                </a>
              </p>
            </div>
          </div>

          <div className="load-in relative" style={{ animationDelay: "300ms" }}>
            <div
              aria-hidden
              className="pointer-events-none absolute -top-14 -right-2 z-20 hidden sm:block"
            >
              <Mascot
                priority
                className="h-auto w-28 drop-shadow-2xl lg:w-36"
              />
            </div>
            <LiveTicker />
          </div>
        </div>
      </div>
    </section>
  );
}
