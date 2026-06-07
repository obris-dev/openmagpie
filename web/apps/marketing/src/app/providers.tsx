"use client";

import { ThemeProvider } from "next-themes";
import type { ReactNode } from "react";

/**
 * System / light / dark via next-themes, identical wiring to the product app
 * so the marketing site, auth, and app all flip together.
 */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
    >
      {children}
    </ThemeProvider>
  );
}
