"use client";

import { useState, type FormEvent } from "react";

/**
 * Inline waitlist capture: email + submit, with a success state. No backend
 * yet, the submit is a placeholder until a form provider is chosen.
 */
export function WaitlistForm({ idPrefix = "wl" }: { idPrefix?: string }) {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState(false);

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!email.trim()) return;
    // TODO: POST to the chosen waitlist provider (Loops / ConvertKit / etc.).
    setDone(true);
  }

  if (done) {
    return (
      <div className="flex items-center gap-3 rounded-md border border-signal/40 bg-signal/10 px-4 py-3.5">
        <span
          aria-hidden
          className="grid size-6 shrink-0 place-items-center rounded-full bg-signal text-sm font-bold text-paper"
        >
          ✓
        </span>
        <p className="text-sm text-ink dark:text-paper">
          You&apos;re on the list. We&apos;ll email{" "}
          <span className="font-medium">{email}</span> when the managed version
          opens.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} className="flex w-full flex-col gap-2.5 sm:flex-row">
      <label htmlFor={`${idPrefix}-email`} className="sr-only">
        Email address
      </label>
      <input
        id={`${idPrefix}-email`}
        type="email"
        required
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="you@company.com"
        className="min-w-0 flex-1 rounded-md border border-ink/15 bg-paper px-4 py-3 text-sm text-ink outline-none transition-colors placeholder:text-ink-subtle focus:border-signal focus:ring-2 focus:ring-signal/25 dark:border-paper/15 dark:bg-ink dark:text-paper dark:placeholder:text-paper/40"
      />
      <button
        type="submit"
        className="shrink-0 rounded-md bg-signal px-6 py-3 text-sm font-semibold text-paper transition-colors hover:bg-signal-600 dark:hover:bg-signal-700"
      >
        Join the waitlist
      </button>
    </form>
  );
}
