import type { AnchorHTMLAttributes, ReactNode } from "react";
import { links } from "../_lib/constants";
import { GithubIcon } from "./icons";

/**
 * Link to the GitHub repo (always opens in a new tab). Icon + label and styling
 * are caller-controlled via className / iconClassName / children, so it works
 * both inline in a sentence and as a standalone chrome link.
 */
export function GithubLink({
  className = "",
  iconClassName = "size-4",
  children,
  ...rest
}: {
  className?: string;
  iconClassName?: string;
  children?: ReactNode;
} & AnchorHTMLAttributes<HTMLAnchorElement>) {
  return (
    <a
      href={links.github}
      target="_blank"
      rel="noreferrer noopener"
      className={className}
      {...rest}
    >
      <GithubIcon className={iconClassName} />
      {children}
    </a>
  );
}
