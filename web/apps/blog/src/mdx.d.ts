// A post's `.mdx` file exports its content component (default) plus a `meta`
// object the registry + route read. `sections` is injected by the rehype TOC plugin.
declare module "*.mdx" {
  import type { ComponentType } from "react";

  export const meta: {
    title: string;
    date: string;
    description: string;
    author?: string;
  };
  export const sections: { title: string; id: string }[];
  const MDXComponent: ComponentType<Record<string, unknown>>;
  export default MDXComponent;
}
