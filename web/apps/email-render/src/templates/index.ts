import type { FC } from "react";
import { WaitlistWelcome } from "./waitlist-welcome";

// Registry the server renders by name. Keys are the `template` values the core
// EmailService sends (the cross-service contract — see
// apps/core/waitlist/services/waitlist.py). `earlyAccessInvite` lands here when
// the invite flow is built. FC<any> because the templates have heterogeneous
// prop shapes; getTemplate() looks them up by own-key only.
export const emailTemplates: Record<string, FC<any>> = {
  waitlistWelcome: WaitlistWelcome,
};
