import { LayoutDashboard } from "lucide-react";
import Link from "next/link";

import { GridBackground } from "@/components/layout/grid-background";
import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";

export default function HomePage() {
  return (
    <div className="flex min-h-svh flex-col">
      <SiteHeader>
        <Link href="/login" className={buttonVariants({ variant: "ghost" })}>
          Log in
        </Link>
        <Link href="/signup" className={buttonVariants()}>
          Sign up
        </Link>
      </SiteHeader>

      <main className="relative isolate flex flex-1 flex-col items-center justify-center px-6 py-24 text-center">
        <GridBackground />

        <h1 className="text-5xl font-semibold tracking-tight sm:text-6xl">
          ProfPilot AI
        </h1>
        <p className="mt-5 max-w-xl text-lg text-balance text-muted-foreground sm:text-xl">
          One intelligent workspace for teaching, research, communication, and
          academic work.
        </p>

        {/* Placeholder until the Professor Workspace is built. */}
        <Empty className="mt-14 max-w-md flex-none border border-foreground/15 bg-background">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <LayoutDashboard />
            </EmptyMedia>
            <EmptyTitle>Professor Workspace</EmptyTitle>
            <EmptyDescription>
              Your workspace is being built. Tools for teaching, research, and
              communication will appear here.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Badge variant="outline">
              <span className="size-1.5 rounded-full bg-amber-500" />
              In development
            </Badge>
          </EmptyContent>
        </Empty>
      </main>

      <SiteFooter />
    </div>
  );
}
