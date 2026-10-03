import type { Metadata } from "next";
import { Geist } from "next/font/google";

import { ThemeProvider } from "@/components/theme-provider";
import { cn } from "@/lib/utils";

import "./globals.css";

const geist = Geist({ subsets: ["latin"], variable: "--font-sans" });

export const metadata: Metadata = {
  // Pages set their own title, shown as e.g. "Log in · ProfPilot AI".
  title: { default: "ProfPilot AI", template: "%s · ProfPilot AI" },
  description:
    "One intelligent workspace for teaching, research, communication, and academic work.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // suppressHydrationWarning: next-themes sets the theme class on <html> before React hydrates.
    <html
      lang="en"
      className={cn("font-sans antialiased", geist.variable)}
      suppressHydrationWarning
    >
      <body>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
