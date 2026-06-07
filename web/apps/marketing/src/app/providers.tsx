"use client";

import { ThemeProvider } from "next-themes";
import type { ReactNode } from "react";
import { NotificationProvider } from "@magpie/ui";

/**
 * System / light / dark via next-themes, identical wiring to the product app
 * so the marketing site, auth, and app all flip together. NotificationProvider
 * sits inside so toasts render under the active theme.
 */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
    >
      <NotificationProvider>{children}</NotificationProvider>
    </ThemeProvider>
  );
}
