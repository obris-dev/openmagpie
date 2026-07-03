import clsx from "clsx";
import type { ElementType, ComponentPropsWithoutRef } from "react";

type ProseProps<T extends ElementType> = {
  as?: T;
  className?: string;
} & Omit<ComponentPropsWithoutRef<T>, "as" | "className">;

export function Prose<T extends ElementType = "div">({
  as,
  className,
  ...props
}: ProseProps<T>) {
  const Component = as ?? "div";
  return (
    <Component
      className={clsx(
        className,
        // Neutral zinc/invert theme; links re-tinted to the brand accent in globals.css.
        "prose prose-zinc max-w-none dark:prose-invert",
        "prose-headings:font-sans prose-headings:tracking-tight",
      )}
      {...props}
    />
  );
}
