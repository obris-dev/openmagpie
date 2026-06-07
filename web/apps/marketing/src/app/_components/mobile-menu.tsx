"use client";

import { useState } from "react";
import { Bars3Icon, XMarkIcon } from "@heroicons/react/24/outline";
import { links } from "./constants";

const navLinks = [
  { href: "#how", label: "How it works" },
  { href: "#where", label: "Where it listens" },
  { href: "#why", label: "Why self-host" },
];

/**
 * Hamburger menu for < lg (mobile + tablet). Toggles a full-width drawer below
 * the header with the section links, GitHub, and the waitlist CTA. Mirrors the
 * obris marketing mobile menu.
 */
export function MobileMenu() {
  const [open, setOpen] = useState(false);

  return (
    <div className="min-[880px]:hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Toggle menu"
        aria-expanded={open}
        className="cursor-pointer rounded-md p-2 text-ink-muted hover:text-ink dark:text-ink-subtle dark:hover:text-paper"
      >
        {open ? <XMarkIcon className="size-5" /> : <Bars3Icon className="size-5" />}
      </button>

      {open && (
        <div className="absolute inset-x-0 top-full border-b border-ink/10 bg-paper/95 backdrop-blur-xl dark:border-paper/10 dark:bg-ink/95 sm:inset-x-auto sm:right-4 sm:mt-2 sm:w-64 sm:rounded-2xl sm:border sm:shadow-xl sm:shadow-ink/10 dark:sm:shadow-black/30">
          <div className="mx-auto flex max-w-6xl flex-col gap-1 px-6 py-4 sm:px-2 sm:py-2">
            {navLinks.map((l) => (
              <a
                key={l.href}
                href={l.href}
                onClick={() => setOpen(false)}
                className="rounded-md px-3 py-2 text-sm font-medium text-ink-muted hover:bg-paper-soft hover:text-ink dark:text-ink-subtle dark:hover:bg-ink-soft dark:hover:text-paper"
              >
                {l.label}
              </a>
            ))}
            <div className="mt-1 border-t border-ink/10 pt-2 dark:border-paper/10">
              <a
                href={links.waitlist}
                onClick={() => setOpen(false)}
                className="block rounded-md bg-signal px-3 py-2 text-center text-sm font-semibold text-paper transition-colors hover:bg-signal-600 dark:hover:bg-signal-700"
              >
                Join the waitlist
              </a>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
