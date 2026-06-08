"use client";

import { useState, type FormEvent } from "react";
import { Button, Input, useNotification } from "@magpie/ui";
import {
  waitlistActions,
  WAITLIST_SOURCE,
  type WaitlistSource,
} from "@magpie/api-utils/waitlist";

/**
 * Inline waitlist capture, two steps. Step 1 is email + submit, posted to the
 * public `/v1/waitlist` endpoint (idempotent server-side). On success the form
 * swaps for a confirmation card that asks ONE optional question (which sources
 * they most want supported next) and records the answer with a second
 * idempotent post (same email, now carrying the vote). The email is already
 * captured, so the question never gates signup: toggle any sources (and/or
 * "Something else" with a note), submit, or skip.
 *
 * `idPrefix` doubles as the `source` so we can tell which form converted (hero
 * vs cta). Input and Button come from @magpie/ui.
 */
const PHASE = {
  FORM: "form",
  ASK: "ask",
  THANKS: "thanks",
} as const;
type Phase = (typeof PHASE)[keyof typeof PHASE];

// The not-yet-shipped roadmap connectors. As one ships, drop it here (and from
// the server enum). "Something else" (OTHER) is rendered separately so it can
// reveal a free-text note.
const SOURCE_OPTIONS: { value: WaitlistSource; label: string }[] = [
  { value: WAITLIST_SOURCE.LINKEDIN, label: "LinkedIn" },
  { value: WAITLIST_SOURCE.SLACK, label: "Slack" },
  { value: WAITLIST_SOURCE.X, label: "X" },
  { value: WAITLIST_SOURCE.BLUESKY, label: "Bluesky" },
  { value: WAITLIST_SOURCE.MASTODON, label: "Mastodon" },
  { value: WAITLIST_SOURCE.GITHUB, label: "GitHub" },
  { value: WAITLIST_SOURCE.HACKER_NEWS, label: "Hacker News" },
];

export function WaitlistForm({ idPrefix = "wl" }: { idPrefix?: string }) {
  const [email, setEmail] = useState("");
  const [phase, setPhase] = useState<Phase>(PHASE.FORM);
  const [submitting, setSubmitting] = useState(false);
  const [voting, setVoting] = useState(false);
  const [selected, setSelected] = useState<WaitlistSource[]>([]);
  const [otherText, setOtherText] = useState("");
  const notify = useNotification();

  const fail = () =>
    notify({
      title: "Something went wrong",
      body: "We had trouble adding you to the list. Please try again.",
      isError: true,
    });

  const otherSelected = selected.includes(WAITLIST_SOURCE.OTHER);
  const isSelected = (v: WaitlistSource) => selected.includes(v);
  const toggle = (v: WaitlistSource) =>
    setSelected((s) => (s.includes(v) ? s.filter((x) => x !== v) : [...s, v]));

  // Submit needs at least one pick, and a note if "Something else" is chosen.
  const canSubmit =
    selected.length > 0 && (!otherSelected || otherText.trim().length > 0);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!email.trim() || submitting) return;
    setSubmitting(true);
    try {
      const { ok } = await waitlistActions.submit(email.trim(), idPrefix);
      if (ok) setPhase(PHASE.ASK);
      else fail();
    } catch {
      fail();
    } finally {
      setSubmitting(false);
    }
  }

  async function submitVote(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!canSubmit || voting) return;
    setVoting(true);
    try {
      // Second idempotent post: same email, now carrying the vote. The email is
      // already safe, so a failure here just lets them retry, never a dead end.
      const { ok } = await waitlistActions.submit(
        email.trim(),
        idPrefix,
        selected,
        otherText.trim() || undefined,
      );
      if (ok) setPhase(PHASE.THANKS);
      else fail();
    } catch {
      fail();
    } finally {
      setVoting(false);
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

  // ASK + THANKS share the confirmation shell; the question collapses to a
  // closing line once they submit a vote (or skip).
  return (
    <div className="rounded-md border border-signal/40 bg-signal/10 px-4 py-3.5">
      <div role="status" aria-live="polite" className="flex items-start gap-3">
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

      {phase === PHASE.ASK ? (
        <form onSubmit={submitVote} className="mt-4 border-t border-signal/20 pt-3.5">
          <p
            id={`${idPrefix}-source-q`}
            className="text-sm text-ink/70 dark:text-paper/70"
          >
            Which sources should we add next? Pick any that apply.
          </p>
          <div
            role="group"
            aria-labelledby={`${idPrefix}-source-q`}
            className="mt-3 flex flex-wrap gap-2"
          >
            {SOURCE_OPTIONS.map((opt) => (
              <Button
                key={opt.value}
                type="button"
                variant={isSelected(opt.value) ? "primary" : "outline"}
                size="sm"
                aria-pressed={isSelected(opt.value)}
                onClick={() => toggle(opt.value)}
              >
                {opt.label}
              </Button>
            ))}
            <Button
              type="button"
              variant={otherSelected ? "primary" : "outline"}
              size="sm"
              aria-pressed={otherSelected}
              onClick={() => {
                if (otherSelected) setOtherText(""); // clear the note on deselect
                toggle(WAITLIST_SOURCE.OTHER);
              }}
            >
              Something else
            </Button>
          </div>

          {otherSelected ? (
            <div className="mt-2.5">
              <label htmlFor={`${idPrefix}-source-other`} className="sr-only">
                Which source?
              </label>
              <Input
                id={`${idPrefix}-source-other`}
                value={otherText}
                onChange={(e) => setOtherText(e.target.value)}
                placeholder="Which source?"
                maxLength={120}
                autoFocus
                className="w-full"
              />
            </div>
          ) : null}

          <div className="mt-3 flex items-center gap-3">
            <Button type="submit" size="sm" loading={voting} disabled={!canSubmit}>
              {voting ? "Submitting…" : "Submit"}
            </Button>
            <button
              type="button"
              onClick={() => setPhase(PHASE.THANKS)}
              disabled={voting}
              className="cursor-pointer text-xs text-ink/50 underline-offset-2 hover:underline disabled:opacity-50 dark:text-paper/50"
            >
              Skip
            </button>
          </div>
        </form>
      ) : (
        <p className="mt-2.5 text-xs text-ink/60 dark:text-paper/60">
          Thanks for the help! We&apos;ll be in touch.
        </p>
      )}
    </div>
  );
}
