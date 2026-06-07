import { Emblem, Mascot } from "@magpie/ui";
import { WaitlistForm } from "./waitlist-form";
import { Container, Heading, Lead } from "./section";

export function Cta() {
  return (
    <section id="waitlist" className="relative">
      <Container className="py-20 sm:py-28">
        <div className="reveal relative overflow-hidden rounded-3xl border border-ink/10 bg-paper-soft px-6 py-16 sm:px-14 dark:border-paper/10 dark:bg-ink-soft">
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0 opacity-[0.4] [background-image:linear-gradient(rgba(0,183,195,0.06)_1px,transparent_1px),linear-gradient(90deg,rgba(0,183,195,0.06)_1px,transparent_1px)] [background-size:40px_40px] [mask-image:radial-gradient(ellipse_at_center,black,transparent_80%)]"
          />
          <div
            aria-hidden
            className="pointer-events-none absolute -right-6 -bottom-10 hidden sm:block"
          >
            <Mascot
              className="h-auto w-48 opacity-20 mix-blend-multiply lg:w-56 dark:opacity-25 dark:mix-blend-screen"
              sizes="(min-width: 1024px) 224px, 192px"
            />
          </div>
          <div className="relative z-10 max-w-3xl">
            <Emblem size={48} />
            <Heading>Don&apos;t want to run it yourself?</Heading>
            <Lead className="mt-4 max-w-xl">
              You&apos;ve already got enough on your plate, keeping another system
              up shouldn&apos;t be yet another thing to worry about. We&apos;ll
              handle it, sign up to get notified when early access opens.
            </Lead>
            <div className="mt-8 max-w-md">
              <WaitlistForm idPrefix="cta" />
            </div>
            <p className="mt-4 text-sm text-ink-subtle dark:text-paper/55">
              Or self-host it free, CLI today with a web UI on the way.
            </p>
          </div>
        </div>
      </Container>
    </section>
  );
}
