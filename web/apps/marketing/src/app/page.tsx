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
        {/* Alternating bands set here so the section rhythm lives in one place. */}
        <Problem />
        <HowItWorks band />
        <WhereItListens />
        <Comparison band />
        <Cta />
      </main>
      <Footer />
    </>
  );
}
