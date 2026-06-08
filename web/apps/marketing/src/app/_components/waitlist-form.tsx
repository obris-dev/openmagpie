"use client";

import { useState, type FormEvent } from "react";
import { Button, Input, useNotification } from "@magpie/ui";
import {
  waitlistActions,
  WAITLIST_CATEGORY,
  type WaitlistCategory,
} from "@magpie/api-utils/waitlist";

/**
 * Inline waitlist capture, two steps. Step 1 is email + submit, posted to the
 * public `/v1/waitlist` endpoint (idempotent server-side). On success the form
 * swaps for a confirmation card that asks ONE optional question — which offering
 * they're waiting for — and records the answer with a second idempotent post
 * (same email, now carrying `category`). The email is already captured, so the
 * question never gates signup: they can pick, skip, or ignore it.
 *
 * `idPrefix` doubles as the `source` so we can tell which form converted (hero
 * vs cta). Input and Button come from @magpie/ui.
 */
const PHASE = {
  FORM: "form",
  CATEGORY: "category",
  THANKS: "thanks",
} as const;
type Phase = (typeof PHASE)[keyof typeof PHASE];

// Today the functional interface is the CLI, so the split is "I'll run it, just
// give me a UI" vs "you run it for me" vs "no preference". Values mirror the
// server's WaitlistCategory; labels carry the who-runs-it cue so the choice is
// unambiguous (cloud has a UI too).
const CATEGORY_OPTIONS: {
  value: WaitlistCategory;
  label: string;
  hint: string;
}[] = [
  { value: WAITLIST_CATEGORY.WEB_UI, label: "A web UI", hint: "I'll self-host" },
  { value: WAITLIST_CATEGORY.CLOUD, label: "Cloud", hint: "you run it for me" },
  { value: WAITLIST_CATEGORY.EITHER, label: "Either's fine", hint: "no preference" },
];

export function WaitlistForm({ idPrefix = "wl" }: { idPrefix?: string }) {
  const [email, setEmail] = useState("");
  const [phase, setPhase] = useState<Phase>(PHASE.FORM);
  const [submitting, setSubmitting] = useState(false);
  const [picking, setPicking] = useState<WaitlistCategory | null>(null);
  const notify = useNotification();

  const fail = () =>
    notify({
      title: "Something went wrong",
      body: "We had trouble adding you to the list. Please try again.",
      isError: true,
    });

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!email.trim() || submitting) return;
    setSubmitting(true);
    try {
      const { ok } = await waitlistActions.submit(email.trim(), idPrefix);
      if (ok) setPhase(PHASE.CATEGORY);
      else fail();
    } catch {
      fail();
    } finally {
      setSubmitting(false);
    }
  }

  async function onPick(category: WaitlistCategory) {
    if (picking) return;
    setPicking(category);
    try {
      // Second idempotent post: same email, now carrying the pick. The email is
      // already safe, so a failure here just lets them retry — never a dead end.
      const { ok } = await waitlistActions.submit(email.trim(), idPrefix, category);
      if (ok) setPhase(PHASE.THANKS);
      else fail();
    } catch {
      fail();
    } finally {
      setPicking(null);
    }
  }

  if (phase === PHASE.FORM) {
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
          name="email"
          type="email"
          autoComplete="email"
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

  // CATEGORY + THANKS share the confirmation shell; the question collapses to a
  // closing line once they pick (or skip).
  return (
    <div
      role="status"
      aria-live="polite"
      className="rounded-md border border-signal/40 bg-signal/10 px-4 py-3.5"
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-signal text-sm font-bold text-paper"
        >
          ✓
        </span>
        <div>
          <p className="text-base font-semibold text-ink dark:text-paper">
            You&apos;re on the list!
          </p>
          <p className="mt-1 text-sm text-ink/70 dark:text-paper/70">
            We&apos;ll email{" "}
            <span className="font-medium text-ink dark:text-paper">{email}</span>{" "}
            when the hosted version opens.
          </p>
        </div>
      </div>

      {phase === PHASE.CATEGORY ? (
        <div className="mt-4 border-t border-signal/20 pt-3.5">
          <p
            id={`${idPrefix}-category-q`}
            className="text-sm text-ink/70 dark:text-paper/70"
          >
            While you wait, what are you most interested in? It helps us decide
            what to build next.
          </p>
          <div
            role="group"
            aria-labelledby={`${idPrefix}-category-q`}
            className="mt-3 flex flex-col gap-2 sm:flex-row"
          >
            {CATEGORY_OPTIONS.map((opt) => (
              <Button
                key={opt.value}
                type="button"
                variant="outline"
                size="sm"
                fullWidth
                loading={picking === opt.value}
                disabled={picking !== null}
                onClick={() => onPick(opt.value)}
                className="flex-1 flex-col items-start gap-0.5 py-2 text-left"
              >
                <span className="font-medium">{opt.label}</span>
                <span className="text-xs font-normal opacity-70">{opt.hint}</span>
              </Button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => setPhase(PHASE.THANKS)}
            disabled={picking !== null}
            className="mt-2.5 cursor-pointer text-xs text-ink/50 underline-offset-2 hover:underline disabled:opacity-50 dark:text-paper/50"
          >
            Skip
          </button>
        </div>
      ) : (
        <p className="mt-2.5 text-xs text-ink/60 dark:text-paper/60">
          Thanks for the help!
        </p>
      )}
    </div>
  );
}
