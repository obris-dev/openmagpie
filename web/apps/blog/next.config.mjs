import nextMDX from "@next/mdx";
import { recmaPlugins } from "./src/mdx/recma.mjs";
import { rehypePlugins } from "./src/mdx/rehype.mjs";
import { remarkPlugins } from "./src/mdx/remark.mjs";

const withMDX = nextMDX({
  options: { remarkPlugins, rehypePlugins, recmaPlugins },
});

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Served under openmagpie.ai/blog in prod (a Cloudflare Worker route sends
  // /blog* here) and localhost:3002/blog in dev. basePath makes every route,
  // internal link, and asset (/blog/_next/...) resolve under /blog, so the
  // apex domain keeps the SEO authority while this stays a separate app.
  // Must match BLOG_BASE_PATH in @magpie/api-utils/site (Next config can't
  // import it (it loads before transpilePackages), so keep them in sync).
  basePath: "/blog",
  transpilePackages: [
    "@magpie/api-utils",
    "@magpie/tailwind-config",
    "@magpie/ui",
  ],
  // Posts are MDX *content* imported by a .tsx route (not `page.mdx` routes),
  // so the route owns `metadata` and the MDX stays a plain component.
  pageExtensions: ["ts", "tsx"],
  // Nothing here is meant to be embedded; deny framing everywhere (mirrors marketing).
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
    ];
  },
};

export default withMDX(nextConfig);
