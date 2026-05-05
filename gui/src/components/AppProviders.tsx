import type { ReactNode } from "react";
import { ThemeProvider } from "../design-system/ThemeProvider";

export function AppProviders({ children }: { children: ReactNode }) {
  return <ThemeProvider>{children}</ThemeProvider>;
}
