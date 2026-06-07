import type { ReactNode } from "react";

/**
 * Centered page container (max width + horizontal padding). Vertical padding is
 * the caller's, since it varies (hero vs section vs cta).
 */
export function Container({
  className = "",
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return <div className={`mx-auto max-w-6xl px-6 ${className}`}>{children}</div>;
}

/**
 * A content section: the shared shell (hairline divider + standard vertical
 * rhythm) wrapping a Container, with an optional alternating background band.
 * page.tsx decides which sections get `band`, so the rhythm lives in one place.
 */
export function Section({
  id,
  band = false,
  className = "",
  children,
}: {
  id?: string;
  band?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      id={id}
      className={`relative border-b border-ink/10 dark:border-paper/10 ${band ? "bg-paper-soft/40 dark:bg-ink-soft/30" : ""} ${className}`}
    >
      <Container className="py-20 sm:py-28">{children}</Container>
    </section>
  );
}

/** Section display heading (h2). Sits below a ConsoleLabel; add max-width as needed. */
export function Heading({
  className = "",
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <h2
      className={`mt-5 text-3xl font-bold tracking-tight text-balance sm:text-5xl ${className}`}
    >
      {children}
    </h2>
  );
}

/** Section lead paragraph. */
export function Lead({
  className = "",
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <p
      className={`text-lg leading-relaxed text-ink-muted dark:text-paper/70 ${className}`}
    >
      {children}
    </p>
  );
}
