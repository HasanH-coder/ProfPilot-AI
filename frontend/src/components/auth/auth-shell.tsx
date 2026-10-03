import type { ReactNode } from "react";

import { GridBackground } from "@/components/layout/grid-background";
import { SiteHeader } from "@/components/layout/site-header";

/** Page frame for the login and signup pages: the site header and a centred card. */
export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-svh flex-col">
      <SiteHeader />
      <main className="relative isolate flex flex-1 items-center justify-center px-6 py-16">
        <GridBackground />
        {children}
      </main>
    </div>
  );
}
