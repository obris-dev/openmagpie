import { Nav } from "./_components/nav";
import { Hero } from "./_components/hero";
import { Problem } from "./_components/problem";
import { HowItWorks } from "./_components/how-it-works";
import { WhereItListens } from "./_components/where-it-listens";
import { Comparison } from "./_components/compare";
import { Cta } from "./_components/cta";
import { Footer } from "./_components/footer";

export default function LandingPage() {
  return (
    <>
      <Nav />
      <main>
        <Hero />
        <Problem />
        <div className="bg-paper-soft/40 dark:bg-ink-soft/30">
          <HowItWorks />
        </div>
        <WhereItListens />
        <div className="bg-paper-soft/40 dark:bg-ink-soft/30">
          <Comparison />
        </div>
        <Cta />
      </main>
      <Footer />
    </>
  );
}
