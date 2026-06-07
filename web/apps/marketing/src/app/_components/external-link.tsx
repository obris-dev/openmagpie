import type { ReactNode } from "react";
import { ArrowUpRight } from "./icons";

/** Inline text link that leaves the site, marked with the ↗ glyph. */
export function ExternalLink({
  href,
  children,
  className = "",
}: {
  href: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className={`inline-flex items-center gap-0.5 underline-offset-4 hover:underline ${className}`}
    >
      {children}
      <ArrowUpRight className="size-3.5 opacity-70" />
    </a>
  );
}
