import { Spinner } from "@/components/ui/spinner";
import { stageLabel } from "@/lib/api/runs";
import type { Run } from "@/lib/api/types";

type JobProgressProps = {
  run: Run | null;
  /** Shown before the job reports its first stage. */
  startingLabel?: string;
  note?: string;
};

/** What a long AI job is doing now, in words. No made-up percentages. */
export function JobProgress({ run, startingLabel = "Starting…", note }: JobProgressProps) {
  return (
    <div role="status" aria-live="polite" className="flex items-start gap-3 rounded-xl border bg-card p-4">
      <Spinner className="mt-0.5 size-5 shrink-0" aria-hidden />
      <div className="flex min-w-0 flex-col gap-0.5">
        <p className="font-medium">{run ? stageLabel(run.stage) : startingLabel}</p>
        <p className="text-sm text-muted-foreground">
          {note ?? "This can take a few minutes. You can leave this page; ProfPilot keeps working."}
        </p>
      </div>
    </div>
  );
}
