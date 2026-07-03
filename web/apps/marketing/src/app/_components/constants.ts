import { origins } from "@magpie/api-utils/site";

/** Outbound links used across the marketing site. */
export const links = {
  github: "https://github.com/obris-dev/openmagpie",
  docs: "https://github.com/obris-dev/openmagpie#readme",
  // Env-aware: localhost:3002 in dev, blog.openmagpie.ai in prod.
  blog: origins.blog,
  // TODO: point at the hosted waitlist form once a provider is chosen.
  waitlist: "#waitlist",
};
