// Custom Worker entry wrapping the OpenNext-generated handler.
//
// Intercepts `curl`/`wget` requests to the root path and serves the quickstart
// installer (scripts/quickstart/bootstrap.sh, copied to public/install.sh at
// build time), so the documented one-liner works:
//
//   curl -fsSL https://openmagpie.ai | sh
//
// wget is matched too as a quiet fallback (the README only advertises curl).
// Every other request (a browser, a bot, anything else) passes straight through
// to the Next.js marketing site. Wired in via wrangler's `main`.
import worker from "./.open-next/worker.js";

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const ua = (request.headers.get("user-agent") || "").toLowerCase();

    if (url.pathname === "/" && (ua.startsWith("curl") || ua.startsWith("wget"))) {
      // Serve the static asset as plain text. Propagate its status so a missing
      // install.sh surfaces as a 404 rather than a 200 with an empty body.
      const script = await env.ASSETS.fetch(new URL("/install.sh", request.url));
      return new Response(script.body, {
        status: script.status,
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }

    return worker.fetch(request, env, ctx);
  },
};
