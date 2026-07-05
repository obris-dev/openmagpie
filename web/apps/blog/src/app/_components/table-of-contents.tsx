"use client";

import { useEffect, useState } from "react";
import type { Section } from "@/app/_lib/posts";

// Sticky sidebar TOC with scroll-spy: highlights the section currently in view
// via an IntersectionObserver over the post's h2 ids (rendered by the content).
// Desktop only; the mobile collapsible lives in ArticleLayout.
export function TableOfContents({ sections }: { sections: Section[] }) {
  const [activeId, setActiveId] = useState<string>(sections[0]?.id ?? "");

  useEffect(() => {
    const headings = sections
      .map((section) => document.getElementById(section.id))
      .filter((el): el is HTMLElement => el !== null);
    if (headings.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        // Highlight the topmost heading currently intersecting the trigger band.
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort(
            (a, b) => a.boundingClientRect.top - b.boundingClientRect.top,
          );
        if (visible[0]) setActiveId(visible[0].target.id);
      },
      // Trigger band is the top ~30% of the viewport, so the active item flips
      // as a heading reaches the top rather than only at dead center.
      { rootMargin: "0px 0px -70% 0px", threshold: 0 },
    );

    headings.forEach((heading) => observer.observe(heading));
    return () => observer.disconnect();
  }, [sections]);

  return (
    <nav aria-label="Table of contents" className="sticky top-24">
      <h2 className="mb-4 text-xs font-semibold uppercase tracking-wider text-ink-subtle">
        On this page
      </h2>
      <ul className="border-l border-ink/10 dark:border-paper/10">
        {sections.map((section) => {
          const active = section.id === activeId;
          return (
            <li key={section.id}>
              <a
                href={`#${section.id}`}
                aria-current={active ? "location" : undefined}
                className={`-ml-px block border-l-2 py-1.5 pl-4 text-sm transition-colors ${
                  active
                    ? "border-signal-500 font-medium text-signal-600 dark:border-signal-400 dark:text-signal-400"
                    : "border-transparent text-ink-subtle hover:text-ink dark:hover:text-paper"
                }`}
              >
                {section.title}
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
