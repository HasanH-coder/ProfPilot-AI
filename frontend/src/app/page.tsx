import { ClipboardCheck } from "lucide-react";
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

        {/* What the workspace offers today, and what comes next. */}
        <Empty className="mt-14 max-w-md flex-none border border-foreground/15 bg-background">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <ClipboardCheck />
            </EmptyMedia>
            <EmptyTitle>Assessment setup</EmptyTitle>
            <EmptyDescription>
              Choose a course, upload course material and previous exams, and
              describe the exam you want, in your own words.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Badge variant="outline">
              <span className="size-1.5 rounded-full bg-amber-500" />
              AI exam generation in development
            </Badge>
          </EmptyContent>
        </Empty>
      </main>

      <SiteFooter />
    </div>
  );
}
