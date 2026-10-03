import { Skeleton } from "@/components/ui/skeleton";

/**
 * Shown right away while a workspace page loads, so clicking a link always
 * responds, even on a slow connection. The sidebar stays usable meanwhile.
 */
export default function WorkspaceLoading() {
  return (
    <div className="flex flex-col gap-10">
      <p role="status" className="sr-only">
        Loading…
      </p>
      <div className="flex flex-col gap-3">
        <Skeleton className="h-9 w-72 max-w-full" />
        <Skeleton className="h-5 w-full max-w-xl" />
      </div>
      <div className="flex flex-col gap-3">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    </div>
  );
}
