import Link from "next/link";
import type { AnchorHTMLAttributes } from "react";

// In-page anchors (`#`) are plain <a> (no routing/basePath needed); internal
// routes (`/`) go through next/link so the /blog basePath applies and nav stays
// client-side; everything else is external and opens in a new tab.
export function a({
  href,
  children,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement>) {
  // No href (rare in MDX): a plain anchor, never an external target=_blank.
  if (!href) {
    return <a {...props}>{children}</a>;
  }
  if (href.startsWith("#")) {
    return (
      <a href={href} {...props}>
        {children}
      </a>
    );
  }
  // Real internal route (but NOT protocol-relative //host, which is external).
  if (href.startsWith("/") && !href.startsWith("//")) {
    return (
      <Link href={href} {...props}>
        {children}
      </Link>
    );
  }
  // External (incl. protocol-relative): spread props FIRST so MDX-authored
  // attributes can't clobber rel/target.
  return (
    <a href={href} {...props} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}
