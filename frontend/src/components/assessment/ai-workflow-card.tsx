"use client";

import { ArrowRight, BookOpenCheck, ClipboardCheck, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { apiRequest, errorMessage } from "@/lib/api/client";
import { stageLabel } from "@/lib/api/runs";
import type { AiStatus, StartedRun } from "@/lib/api/types";

const SPEC_LABELS = {
  none: "Not interpreted yet",
  draft: "Waiting for your approval",
  approved: "Approved",
  stale: "Out of date",
} as const;

const EXAM_LABELS = { generating: "Being generated", building: "Being built with AI", ready: "Ready", failed: "Stopped" } as const;

/** Where this assessment is in the AI workflow, and the next step. */
export function AiWorkflowCard({ assessmentId }: { assessmentId: string }) {
  const router = useRouter();
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const base = `/workspace/assessments/${assessmentId}`;

  useEffect(() => {
    apiRequest<AiStatus>(`/api/assessments/${assessmentId}/ai-status`)
      .then(setStatus)
      .catch((cause) => setError(errorMessage(cause)));
  }, [assessmentId]);

  async function interpret() {
    setStarting(true);
    try {
      await apiRequest<StartedRun>(`/api/assessments/${assessmentId}/interpretation`, { method: "POST", freshSession: true });
      router.push(`${base}/plan`);
    } catch (cause) {
      setError(errorMessage(cause));
      setStarting(false);
    }
  }

  if (error && !status) {
    return (
      <p className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">
        ProfPilot&apos;s AI features aren&apos;t available right now. {error}
      </p>
    );
  }
  if (!status) return <Skeleton className="h-32 w-full rounded-xl" />;

  const files = status.documents;
  const read = files.filter((file) => file.status === "ready").length;
  const running = status.runs.find((run) => run.status === "running");
  const exam = status.exam;
  const examPath = exam ? `${base}/${exam.mode === "interactive" && exam.status === "building" ? "builder" : "exam"}` : null;

  return (
    <div className="flex flex-col gap-4 rounded-xl border bg-card p-4 sm:p-5">
      <dl className="grid gap-4 sm:grid-cols-3">
        <div className="flex flex-col gap-1">
          <dt className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <BookOpenCheck className="size-3.5" />
            Files
          </dt>
          <dd className="text-sm">
            {files.length === 0 ? "None uploaded" : `${read} of ${files.length} read by ProfPilot`}
            {status.styleProfile && (
              <span className="block text-xs text-muted-foreground">
                Previous-exam style analysed ({status.styleProfile.examsAnalyzed})
              </span>
            )}
          </dd>
        </div>
        <div className="flex flex-col gap-1">
          <dt className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <Sparkles className="size-3.5" />
            Plan
          </dt>
          <dd>
            <Badge variant={status.spec.status === "approved" ? "secondary" : "outline"}>{SPEC_LABELS[status.spec.status]}</Badge>
          </dd>
        </div>
        <div className="flex flex-col gap-1">
          <dt className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <ClipboardCheck className="size-3.5" />
            Exam
          </dt>
          <dd className="text-sm">
            {exam ? (
              <>
                <Badge variant="outline">{EXAM_LABELS[exam.status]}</Badge>{" "}
                <span className="text-muted-foreground">
                  {exam.questionCount} question{exam.questionCount === 1 ? "" : "s"}
                </span>
              </>
            ) : (
              <span className="text-muted-foreground">Not created yet</span>
            )}
          </dd>
        </div>
      </dl>

      {running && (
        <p role="status" className="flex items-center gap-2 text-sm">
          <Spinner className="size-3.5" aria-hidden />
          {stageLabel(running.stage)}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        {exam && examPath ? (
          <Link href={examPath} className={buttonVariants()}>
            Open exam
            <ArrowRight data-icon="inline-end" />
          </Link>
        ) : null}
        {status.spec.status === "none" ? (
          <Button onClick={interpret} disabled={starting || Boolean(running)} variant={exam ? "outline" : "default"}>
            {starting ? <Spinner /> : <Sparkles />}
            Improve with AI
          </Button>
        ) : (
          <Link href={`${base}/plan`} className={buttonVariants({ variant: exam ? "outline" : "default" })}>
            {status.spec.status === "approved" ? "View plan" : "Review the plan"}
          </Link>
        )}
      </div>
    </div>
  );
}
