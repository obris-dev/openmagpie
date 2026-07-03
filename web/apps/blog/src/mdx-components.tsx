import type { MDXComponents } from "mdx/types";
import * as mdxComponents from "@/app/_components/mdx";

// Next looks for this file at the app root to build the global MDX component map.
export function useMDXComponents(components: MDXComponents): MDXComponents {
  return { ...components, ...mdxComponents };
}
