import type { ReactNode } from "react";
import { Mascot, ThemeToggle } from "@magpie/ui";

/**
 * Auth shell:
 *  - Soft radial gradient gives the bg a bit of depth (Linear/Vercel-style).
 *    Light: paper → paper-soft fade. Dark: ink → ink-soft fade with a hint
 *    of signal warmth at the bottom-right.
 *  - Mascot peeks from behind the right edge of the page. Subtle on purpose
 *   , character without overwhelming the form. Hidden on small screens
 *    where the card itself fills the viewport.
 */
export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="relative min-h-dvh overflow-hidden bg-paper-soft dark:bg-ink">
      {/* Layered radial gradients, kept very subtle. */}
      <div
        aria-hidden
        className={[
          "pointer-events-none absolute inset-0",
          "bg-[radial-gradient(ellipse_at_top,_rgba(0,183,195,0.06),_transparent_60%)]",
          "dark:bg-[radial-gradient(ellipse_at_top,_rgba(125,249,255,0.04),_transparent_55%)]",
        ].join(" ")}
      />
      <div
        aria-hidden
        className={[
          "pointer-events-none absolute inset-0",
          "bg-[radial-gradient(ellipse_at_bottom_right,_rgba(0,183,195,0.04),_transparent_55%)]",
          "dark:bg-[radial-gradient(ellipse_at_bottom_right,_rgba(0,183,195,0.10),_transparent_50%)]",
        ].join(" ")}
      />

      {/* Mascot peek, bottom-right, hidden < md. */}
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-10 -right-16 hidden md:block lg:-bottom-6 lg:-right-10"
      >
        <Mascot
          className="h-auto w-[28rem] opacity-[0.18] mix-blend-multiply dark:mix-blend-screen dark:opacity-25 lg:w-[32rem]"
          sizes="(min-width: 1024px) 512px, 448px"
        />
      </div>

      {/* Theme toggle pinned top-right. Auth pages don't have a header to
       * dock it into yet, so absolute-position over the layout. */}
      <div className="absolute right-4 top-4 z-20 sm:right-6 sm:top-6">
        <ThemeToggle />
      </div>

      <div className="relative z-10">{children}</div>
    </div>
  );
}
