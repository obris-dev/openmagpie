import { siteMeta, blogLinkUrl } from "@magpie/api-utils/site";

/** Outbound links used across the marketing site. */
export const links = {
  github: siteMeta.repoUrl,
  docs: `${siteMeta.repoUrl}#readme`,
  // Env-aware link base: localhost:3002/blog in dev, openmagpie.ai/blog in prod.
  // Getter so blogLinkUrl() resolves lazily (its prod-env throw stays out of
  // module load, keeping this import side-effect-free).
  get blog() {
    return blogLinkUrl();
  },
  // TODO: point at the hosted waitlist form once a provider is chosen.
  waitlist: "#waitlist",
};
