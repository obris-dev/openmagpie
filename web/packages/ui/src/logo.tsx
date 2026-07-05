/// <reference types="next/image-types/global" />
// Declares *.svg / *.png as next/image StaticImageData wherever this file is
// compiled (this package standalone, or an app that re-typechecks it), without
// depending on a build-generated next-env.d.ts. Same reference next-env.d.ts
// uses, so TS dedupes it when both are present.
import clsx from "clsx";
import Image from "next/image";
import emblemSrc from "./brand/emblem.svg";
import wordmarkOnLight from "./brand/wordmark-on-light.svg";
import wordmarkOnDark from "./brand/wordmark-on-dark.svg";
import mascotSrc from "./brand/mascot.png";

// Brand assets are bundled into this package and static-imported for in-page
// logos, so those are the single source of truth and Next emits them with the
// correct, basePath-aware /_next/static URL in every consuming app. (Marketing +
// blog also keep a public/brand copy for their build-time OG images, which Satori
// reads off disk; see the sync note in AGENTS.md.)

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
      src={emblemSrc}
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
  /** Preload as an LCP hint. Off by default so ThemedLogo (which renders both
   * variants, one CSS-hidden) doesn't preload an image that never shows. */
  priority?: boolean;
}

/**
 * The full "OpenMagpie" wordmark, emblem + Poppins 600 set text.
 */
export function Logo({
  height = 32,
  on = "light",
  className,
  priority = false,
}: LogoProps) {
  const src = on === "light" ? wordmarkOnLight : wordmarkOnDark;
  // Wordmark viewBox is 1029.53x195.48 (~5.27:1).
  const width = Math.round(height * (1029.53 / 195.48));
  return (
    <Image
      src={src}
      width={width}
      height={height}
      alt="OpenMagpie"
      priority={priority}
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
  /** Rendered width hint so next/image picks a small srcset candidate instead of
   * fetching the full 1224px source (the display size is much smaller). */
  sizes?: string;
}

/**
 * The illustrated magpie holding a gem (1224x1014 artwork). Display size and
 * placement come from `className` (e.g. `w-72 h-auto`); the aspect ratio is
 * preserved. Decorative by default; pass `alt` when it carries meaning. Pass
 * `sizes` to avoid over-fetching the source at small render sizes.
 */
export function Mascot({ alt = "", className, priority, sizes }: MascotProps) {
  return (
    <Image
      src={mascotSrc}
      alt={alt}
      priority={priority}
      sizes={sizes}
      className={className}
    />
  );
}
