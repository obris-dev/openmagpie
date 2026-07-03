"use client";

import clsx from "clsx";
import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { setThemeCookie } from "./theme-provider";

export interface ThemeToggleProps {
  className?: string;
}

/**
 * Sun/moon button that flips between "light" and "dark". On first render
 * before hydration the resolved theme is unknown, so we render an invisible
 * placeholder to avoid a hydration mismatch + visual flash.
 *
 * Clicking always sets an explicit "light" or "dark", opting the user out
 * of system-tracking on purpose. To return to system, they can clear the
 * theme via DevTools or we can add a "system" option later.
 */
export function ThemeToggle({ className }: ThemeToggleProps) {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const isDark = mounted && resolvedTheme === "dark";

  return (
    <button
      type="button"
      onClick={() => {
        const next = isDark ? "light" : "dark";
        setTheme(next);
        setThemeCookie(next); // sync the choice across openmagpie origins
      }}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      title={isDark ? "Switch to light mode" : "Switch to dark mode"}
      className={clsx(
        "inline-flex size-9 items-center justify-center rounded-md transition-colors",
        "text-ink/60 hover:bg-ink/5 hover:text-ink",
        "dark:text-paper/60 dark:hover:bg-paper/5 dark:hover:text-paper",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-signal",
        className,
      )}
    >
      {/* Render both icons; hide one via dark variant. Means no flicker when
       * the theme flips because we don't depend on `mounted` to choose. */}
      <SunIcon className="block size-5 dark:hidden" />
      <MoonIcon className="hidden size-5 dark:block" />
    </button>
  );
}

function SunIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
    </svg>
  );
}

function MoonIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}
