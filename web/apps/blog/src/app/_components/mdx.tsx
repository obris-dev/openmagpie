import Link from "next/link";
import type { AnchorHTMLAttributes, HTMLAttributes } from "react";

// Internal (`/`, `#`) links go through next/link so the basePath (/blog) is
// applied and in-app nav stays client-side; external links open in a new tab.
export function a({
  href,
  children,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement>) {
  if (href && (href.startsWith("/") || href.startsWith("#"))) {
    return (
      <Link href={href} {...props}>
        {children}
      </Link>
    );
  }
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
      {children}
    </a>
  );
}

export function h2({
  children,
  id,
  ...props
}: HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h2 id={id} {...props}>
      {children}
    </h2>
  );
}
