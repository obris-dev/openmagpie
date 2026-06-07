import type { ReactNode } from "react";

/** The shared console kicker: a teal dot + mono uppercase label. */
export function ConsoleLabel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-2.5 font-mono text-xs uppercase tracking-[0.22em] text-signal ${className}`}
    >
      <span aria-hidden className="size-1.5 rounded-full bg-signal" />
      {children}
    </span>
  );
}
