import { ConsoleLabel } from "./console-label";
import { Section, Heading, Lead } from "./section";

export function Problem({ band }: { band?: boolean }) {
  return (
    <Section band={band}>
      <div className="reveal grid gap-8 md:grid-cols-[1fr_1.3fr] md:gap-16">
        <div>
          <ConsoleLabel>The problem</ConsoleLabel>
          <Heading>You&apos;re already doing this by hand.</Heading>
        </div>
        <Lead>
          Every day you check Reddit, Hacker News, and a handful of feeds for the
          same things: someone hitting a problem your product solves, a question
          in your wheelhouse, a mention of you or a competitor. The best threads
          are gone by the time you find them, and a late reply is just noise.
        </Lead>
      </div>
    </Section>
  );
}
