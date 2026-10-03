import { LayoutDashboard } from "lucide-react";

import { Badge } from "@/components/ui/badge";
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
    <section className="relative isolate flex flex-1 flex-col items-center justify-center px-6 py-24 text-center">
      {/* Decorative grid that fades out towards the edges. */}
      <div
        aria-hidden
        className="bg-grid absolute inset-0 -z-10 mask-radial-from-20% mask-radial-to-70%"
      />

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
    </section>
  );
}
