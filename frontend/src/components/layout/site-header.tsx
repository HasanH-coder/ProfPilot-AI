import Link from "next/link";
import type { ReactNode } from "react";

import { Logo } from "@/components/logo";
import { ThemeToggle } from "@/components/theme-toggle";

/** Top bar for public pages. `children` are extra actions shown after the theme toggle. */
export function SiteHeader({ children }: { children?: ReactNode }) {
  return (
    <header className="sticky top-0 z-40 border-b bg-background/80 backdrop-blur">
      <div className="mx-auto flex h-16 w-full max-w-6xl items-center justify-between gap-4 px-6">
        <Link href="/">
          <Logo />
        </Link>
        <div className="flex items-center gap-2">
          <ThemeToggle />
          {children}
        </div>
      </div>
    </header>
  );
}
