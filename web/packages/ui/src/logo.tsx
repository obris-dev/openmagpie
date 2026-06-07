import clsx from "clsx";
import Image from "next/image";

export interface EmblemProps {
  /** Pixel size of the rendered square. */
  size?: number;
  className?: string;
}

/**
 * The 400x400 Signal-disc + Paper-sparkle emblem. Single source of truth,
 * works on light, dark, and photographic backgrounds (the paper sparkle reads
 * fine on every viable surface).
 */
export function Emblem({ size = 48, className }: EmblemProps) {
  return (
    <Image
      src="/brand/emblem.svg"
      width={size}
      height={size}
      alt="OpenMagpie emblem"
      priority
      className={className}
    />
  );
}

export interface LogoProps {
  /** Height in pixels (width scales proportionally). */
  height?: number;
  /** Background context; controls light/dark wordmark variant. */
  on?: "light" | "dark";
  className?: string;
}

/**
 * The full "OpenMagpie" wordmark, emblem + Poppins 600 set text.
 */
export function Logo({ height = 32, on = "light", className }: LogoProps) {
  const src =
    on === "light" ? "/brand/wordmark-on-light.svg" : "/brand/wordmark-on-dark.svg";
  // Wordmark viewBox is 1029.53x195.48 (~5.27:1).
  const width = Math.round(height * (1029.53 / 195.48));
  return (
    <Image
      src={src}
      width={width}
      height={height}
      alt="OpenMagpie"
      priority
      className={clsx("h-auto", className)}
    />
  );
}

export interface MascotProps {
  /** Decorative by default (alt=""); pass a description when it conveys meaning. */
  alt?: string;
  /** Display size + placement (e.g. "w-72 h-auto"); the 1224x1014 aspect is kept. */
  className?: string;
  priority?: boolean;
}

/**
 * The illustrated magpie holding a gem (1224x1014 artwork). Display size and
 * placement come from `className` (e.g. `w-72 h-auto`); the aspect ratio is
 * preserved. Decorative by default; pass `alt` when it carries meaning.
 */
export function Mascot({ alt = "", className, priority }: MascotProps) {
  return (
    <Image
      src="/brand/mascot.png"
      width={1224}
      height={1014}
      alt={alt}
      priority={priority}
      className={className}
    />
  );
}
