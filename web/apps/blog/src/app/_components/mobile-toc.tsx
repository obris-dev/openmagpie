"use client";

import { useEffect, useRef, useState } from "react";
import type { Section } from "@/app/_lib/posts";

// Mobile/tablet TOC: a floating icon button, fixed to the bottom-right so it
// stays reachable while scrolling, opening a drawer that slides in from the
// right (the same side as the desktop sticky sidebar). Both are lg:hidden;
// desktop uses the TableOfContents sidebar instead.
export function MobileToc({ sections }: { sections: Section[] }) {
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  // On open: move focus into the drawer (keyboard users), close on Escape, and
  // lock body scroll. On close: restore focus to the trigger and undo the lock.
  useEffect(() => {
    if (!open) return;
    panelRef.current?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      // Return focus to the trigger on close (no-op on unmount, button gone).
      triggerRef.current?.focus();
    };
  }, [open]);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(true)}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label="On this page"
        className="fixed bottom-6 right-6 z-40 flex size-12 items-center justify-center rounded-full border border-ink/10 bg-paper text-ink-subtle shadow-lg transition-colors hover:text-ink lg:hidden dark:border-paper/10 dark:bg-ink dark:hover:text-paper"
      >
        <svg
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M4 6h16M4 12h10M4 18h7" />
        </svg>
      </button>

      {/* Always mounted so the slide + fade animate both ways; pointer-events
          off while closed so it never blocks taps. */}
      <div
        className={`fixed inset-0 z-50 lg:hidden ${open ? "" : "pointer-events-none"}`}
        role="dialog"
        aria-modal="true"
        aria-label="Table of contents"
        aria-hidden={!open}
        inert={!open}
      >
        <div
          onClick={() => setOpen(false)}
          className={`absolute inset-0 bg-ink/40 backdrop-blur-sm transition-opacity duration-200 dark:bg-ink/60 ${
            open ? "opacity-100" : "opacity-0"
          }`}
        />
        <div
          ref={panelRef}
          tabIndex={-1}
          className={`absolute inset-y-0 right-0 flex w-72 max-w-[80%] flex-col overflow-y-auto bg-paper p-6 shadow-xl outline-none transition-transform duration-200 ease-out dark:bg-ink ${
            open ? "translate-x-0" : "translate-x-full"
          }`}
        >
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-ink-subtle">
              On this page
            </h2>
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Close"
              className="text-ink-subtle transition-colors hover:text-ink dark:hover:text-paper"
            >
              <svg
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          </div>
          <ul className="space-y-3">
            {sections.map((section) => (
              <li key={section.id}>
                <a
                  href={`#${section.id}`}
                  onClick={() => setOpen(false)}
                  className="block text-sm text-ink-muted transition-colors hover:text-signal-600 dark:text-ink-subtle dark:hover:text-signal-400"
                >
                  {section.title}
                </a>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </>
  );
}
