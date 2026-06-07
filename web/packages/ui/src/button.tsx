import clsx from "clsx";
import type { ButtonHTMLAttributes } from "react";

export type ButtonVariant =
  | "primary"
  | "secondary"
  | "outline"
  | "ghost"
  | "danger";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "sm" | "md" | "lg";
  /** Disable the button + leave the rendered children intact (no spinner).
   * Callers swap the label text instead (e.g. "Sign in" → "Signing in…"). */
  loading?: boolean;
  fullWidth?: boolean;
}

const base =
  "inline-flex items-center justify-center cursor-pointer rounded-md font-medium " +
  "transition-colors disabled:pointer-events-none disabled:opacity-50";

const variants: Record<ButtonVariant, string> = {
  // Primary rests on Signal (#00B7C3, the brand teal). Hover darkens to
  // signal-600 in light mode (signal-700 reads too heavy on a light surface)
  // and to signal-700 in dark. Glow is reserved for accents, not the hover.
  primary: "bg-signal text-paper hover:bg-signal-600 dark:hover:bg-signal-700",
  secondary:
    "bg-paper-soft text-ink hover:bg-paper-deep " +
    "dark:bg-ink-soft dark:text-paper dark:hover:bg-ink",
  outline:
    "border border-ink/15 bg-transparent text-ink hover:bg-paper-soft " +
    "dark:border-paper/15 dark:text-paper dark:hover:bg-ink-soft",
  ghost:
    "text-ink hover:bg-paper-soft dark:text-paper dark:hover:bg-ink-soft",
  danger: "bg-red-600 text-white hover:bg-red-700",
};

const sizes = {
  sm: "px-3 py-1.5 text-sm",
  md: "px-4 py-2 text-sm",
  lg: "px-5 py-2.5 text-base",
};

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  fullWidth = false,
  className,
  children,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={clsx(
        base,
        variants[variant],
        sizes[size],
        fullWidth && "w-full",
        className,
      )}
    >
      {children}
    </button>
  );
}
