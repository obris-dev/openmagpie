// Email-render sidecar: renders React Email templates to {html, plainText} on
// demand. The core EmailService POSTs /render (template name + props); the
// mailer drain then sends the returned HTML via Django's email backend. Plain
// Node http server (no framework) — it's an internal backend service called by
// core, not the browser.

import * as http from "http";
import * as React from "react";
import { render } from "@react-email/render";
import { emailTemplates } from "./templates/index";

const PORT = Number(process.env.PORT) || 3010;

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,HEAD,POST,OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

const JSON_HEADERS = { "Content-Type": "application/json", ...corsHeaders };

// Cap the accepted body so a runaway/hostile request can't grow it unbounded.
// Template props are tiny; 1 MiB is ample.
const MAX_BODY_BYTES = 1_048_576;

/** Thrown by readJsonBody past MAX_BODY_BYTES; the handler maps it to 413. */
class PayloadTooLargeError extends Error {}

/** Look up a template by name using OWN keys only, so a prototype key
 * ("__proto__", "constructor", ...) can't resolve to an Object.prototype
 * member (truthy) and blow up rendering — it returns undefined -> a clean 404. */
function getTemplate(name: string | undefined) {
  if (!name || !Object.hasOwn(emailTemplates, name)) return undefined;
  return emailTemplates[name];
}

async function readJsonBody(req: http.IncomingMessage): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    let aborted = false;
    req.on("data", (chunk: Buffer) => {
      if (aborted) return;
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        aborted = true;
        reject(new PayloadTooLargeError());
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      if (aborted) return;
      // Concat then decode ONCE — decoding per chunk corrupts a multi-byte
      // UTF-8 character split across a chunk boundary.
      const body = Buffer.concat(chunks).toString("utf8");
      try {
        resolve(body ? JSON.parse(body) : {});
      } catch {
        resolve({});
      }
    });
    req.on("error", reject);
  });
}

async function handleRequest(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
  if (req.method === "OPTIONS") {
    res.writeHead(204, corsHeaders);
    res.end();
    return;
  }

  const url = new URL(req.url || "/", `http://${req.headers.host}`);
  const path = url.pathname;

  if (path === "/health" && req.method === "GET") {
    res.writeHead(200, JSON_HEADERS);
    res.end(JSON.stringify({ status: "ok", service: "email-render" }));
    return;
  }

  if (path === "/templates" && req.method === "GET") {
    res.writeHead(200, JSON_HEADERS);
    res.end(JSON.stringify({ templates: Object.keys(emailTemplates) }));
    return;
  }

  // POST /render -> { success, html, plainText } (the contract core expects).
  if (path === "/render" && req.method === "POST") {
    const body = await readJsonBody(req);
    const template = body.template as string | undefined;
    const props = (body.props || {}) as Record<string, unknown>;

    if (!template) {
      console.warn("email-render: render -> 400 (template is required)");
      res.writeHead(400, JSON_HEADERS);
      res.end(JSON.stringify({ success: false, error: "template is required" }));
      return;
    }

    const Component = getTemplate(template);
    if (!Component) {
      console.warn(`email-render: render "${template}" -> 404 (unknown template)`);
      res.writeHead(404, JSON_HEADERS);
      res.end(
        JSON.stringify({
          success: false,
          error: `template "${template}" not found`,
          availableTemplates: Object.keys(emailTemplates),
        }),
      );
      return;
    }

    const element = React.createElement(Component, props);
    const html = await render(element);
    const plainText = await render(element, { plainText: true });
    console.log(`email-render: rendered "${template}" -> 200 (${html.length} bytes)`);
    res.writeHead(200, JSON_HEADERS);
    res.end(JSON.stringify({ success: true, template, html, plainText }));
    return;
  }

  // POST /preview -> rendered HTML, for eyeballing a template in a browser.
  if (path === "/preview" && req.method === "POST") {
    const body = await readJsonBody(req);
    const template = body.template as string | undefined;
    const props = (body.props || {}) as Record<string, unknown>;
    const Component = getTemplate(template);
    if (!Component) {
      res.writeHead(404, { "Content-Type": "text/html", ...corsHeaders });
      res.end(`<h1>template "${template ?? ""}" not found</h1>`);
      return;
    }
    const html = await render(React.createElement(Component, props), { pretty: true });
    res.writeHead(200, { "Content-Type": "text/html", ...corsHeaders });
    res.end(html);
    return;
  }

  res.writeHead(404, JSON_HEADERS);
  res.end(JSON.stringify({ success: false, error: "not found" }));
}

const server = http.createServer((req, res) => {
  handleRequest(req, res).catch((error) => {
    if (res.headersSent) return;
    if (error instanceof PayloadTooLargeError) {
      res.writeHead(413, JSON_HEADERS);
      res.end(JSON.stringify({ success: false, error: "payload too large" }));
      return;
    }
    console.error("email-render: request failed:", error);
    res.writeHead(500, JSON_HEADERS);
    res.end(JSON.stringify({ success: false, error: "internal server error" }));
  });
});

server.listen(PORT, () => {
  console.log(`email-render listening on :${PORT} — templates:`, Object.keys(emailTemplates));
});

for (const signal of ["SIGTERM", "SIGINT"] as const) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
