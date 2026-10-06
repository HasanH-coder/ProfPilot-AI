"use client";

import { Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { ApiError, apiRequest, errorMessage } from "@/lib/api/client";
import { stageLabel, useRun } from "@/lib/api/runs";
import type { AiStatus, StartedRun } from "@/lib/api/types";

type ImproveWithAiProps = {
  /** Saves the form (creating the draft if needed). Returns the assessment id, or null if it can't be saved. */
  ensureSaved: () => Promise<string | null>;
  disabled?: boolean;
};

/**
 * "Improve with AI": ProfPilot reads everything provided (settings, notes,
 * instructions, files, previous exams, preferences) and drafts the plan, which
 * the professor reviews and approves on the next page.
 */
export function ImproveWithAi({ ensureSaved, disabled }: ImproveWithAiProps) {
  const router = useRouter();
  const [runId, setRunId] = useState<string | null>(null);
  const [assessmentId, setAssessmentId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { run } = useRun(runId, (finished) => {
    if (finished.status === "succeeded" && assessmentId) {
      router.push(`/workspace/assessments/${assessmentId}/plan`);
    } else {
      setRunId(null);
      setError(finished.errorMessage ?? "ProfPilot couldn't interpret this assessment. Please try again.");
    }
  });

  async function start() {
    setError(null);
    setStarting(true);
    try {
      const id = await ensureSaved();
      if (!id) return;
      setAssessmentId(id);
      try {
        const started = await apiRequest<StartedRun>(`/api/assessments/${id}/interpretation`, {
          method: "POST",
          freshSession: true,
        });
        setRunId(started.run.id);
      } catch (cause) {
        // Already running (e.g. started in another tab): follow that one instead.
        if (cause instanceof ApiError && cause.code === "job_running") {
          const status = await apiRequest<AiStatus>(`/api/assessments/${id}/ai-status`);
          const running = status.runs.find((item) => item.kind === "interpretation" && item.status === "running");
          if (running) {
            setRunId(running.id);
            return;
          }
        }
        throw cause;
      }
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setStarting(false);
    }
  }

  const working = starting || Boolean(runId);
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-dashed p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-muted-foreground sm:max-w-md">
          ProfPilot reads everything on this page, your files and your preferences, then drafts a complete plan for
          you to review before anything is generated.
        </p>
        <Button type="button" onClick={start} disabled={disabled || working} className="shrink-0">
          {working ? <Spinner /> : <Sparkles />}
          Improve with AI
        </Button>
      </div>
      {working && (
        <p role="status" aria-live="polite" className="flex items-center gap-2 text-sm">
          <Spinner className="size-3.5" aria-hidden />
          {starting && !runId ? "Saving your draft…" : stageLabel(run?.stage)}
          <span className="text-muted-foreground">This can take a minute.</span>
        </p>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
    </div>
  );
}
