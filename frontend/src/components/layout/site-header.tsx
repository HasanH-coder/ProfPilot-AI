import Link from "next/link";
import Image from "next/image";
import type { ReactNode } from "react";

import { ThemeToggle } from "@/components/theme-toggle";

/** Top bar for public pages. `children` are extra actions shown after the theme toggle. */
export function SiteHeader({ children }: { children?: ReactNode }) {
  return (
    <header className="public-header">
      <div className="public-header-inner">
        <div className="flex min-w-0 items-center gap-5">
          <Link href="/" className="public-university-brand" aria-label="American University of Beirut — ProfPilot home">
            <Image src="/images/aub-logo.png" alt="American University of Beirut" width={300} height={100} />
          </Link>
          <Link href="/" className="public-product-brand hidden sm:block">
            ProfPilot<span> AI</span>
          </Link>
        </div>
        <nav aria-label="Account" className="flex shrink-0 items-center gap-2 sm:gap-3">
          <ThemeToggle />
          {children}
        </nav>
      </div>
    </header>
  );
}
