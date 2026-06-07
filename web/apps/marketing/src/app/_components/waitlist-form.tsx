"use client";

import { useState, type FormEvent } from "react";
import { Button, Input } from "@magpie/ui";

/**
 * Inline waitlist capture: email + submit, with a success state. No backend
 * yet, the submit is a placeholder until a form provider is chosen. Input and
 * Button come from @magpie/ui; `items-stretch` keeps them the same height in
 * the side-by-side row.
 */
export function WaitlistForm({ idPrefix = "wl" }: { idPrefix?: string }) {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState(false);

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!email.trim()) return;
    // TODO: POST to the chosen waitlist provider once one is set.
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
          <span className="font-medium">{email}</span> when the hosted version
          opens.
        </p>
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="flex w-full flex-col gap-2.5 sm:flex-row sm:items-stretch"
    >
      <label htmlFor={`${idPrefix}-email`} className="sr-only">
        Email address
      </label>
      <Input
        id={`${idPrefix}-email`}
        type="email"
        required
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="you@company.com"
        className="min-w-0 flex-1"
      />
      <Button type="submit" className="shrink-0">
        Join the waitlist
      </Button>
    </form>
  );
}
