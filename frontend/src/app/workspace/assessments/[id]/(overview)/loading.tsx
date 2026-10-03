import { Skeleton } from "@/components/ui/skeleton";

// "(overview)" is a route group: it doesn't change the address. It lets this
// loading screen cover only the overview, so the edit page never shows it, for
// example while a new assessment's first save reopens the form there.

/** Shown right away while an assessment's overview loads. */
export default function AssessmentOverviewLoading() {
  return (
    <div className="flex flex-col gap-10">
      <p role="status" className="sr-only">
        Loading…
      </p>
      <div className="flex flex-col gap-3">
        <Skeleton className="h-4 w-28" />
        <Skeleton className="h-9 w-64 max-w-full" />
        <Skeleton className="h-5 w-80 max-w-full" />
        <div className="mt-4 flex gap-2">
          <Skeleton className="h-8 w-36" />
          <Skeleton className="h-8 w-24" />
        </div>
      </div>
      <Skeleton className="h-10 w-full" />
      <div className="flex flex-col gap-4">
        <Skeleton className="h-6 w-24" />
        <Skeleton className="h-64 w-full rounded-xl" />
      </div>
    </div>
  );
}
