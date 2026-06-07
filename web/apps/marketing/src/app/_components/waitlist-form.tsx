"use client";

import { useState, type FormEvent } from "react";
import { Button, Input, useNotification } from "@magpie/ui";
import { waitlistActions } from "@magpie/api-utils/waitlist";

/**
 * Inline waitlist capture: email + submit. Posts to the public `/v1/waitlist`
 * endpoint (idempotent server-side). Success swaps the form for a persistent
 * confirmation card; failures surface as an error toast (useNotification).
 * `idPrefix` doubles as the `source` so we can tell which form converted (hero
 * vs cta). Input and Button come from @magpie/ui.
 */
export function WaitlistForm({ idPrefix = "wl" }: { idPrefix?: string }) {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const notify = useNotification();

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!email.trim() || submitting) return;
    setSubmitting(true);
    const fail = () =>
      notify({
        title: "Something went wrong",
        body: "We couldn't add you just now. Please try again.",
        isError: true,
      });
    try {
      const { ok } = await waitlistActions.submit(email.trim(), idPrefix);
      if (ok) {
        setDone(true);
      } else {
        fail();
      }
    } catch {
      fail();
    } finally {
      setSubmitting(false);
    }
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
      <Button type="submit" loading={submitting} className="shrink-0">
        {submitting ? "Joining…" : "Join the waitlist"}
      </Button>
    </form>
  );
}
