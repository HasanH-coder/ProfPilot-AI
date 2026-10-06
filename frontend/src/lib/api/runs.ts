"use client";

// Following a long AI job (interpretation, generation, …) while it runs.

import { useEffect, useRef, useState } from "react";

import { apiRequest, errorMessage } from "@/lib/api/client";
import type { Run } from "@/lib/api/types";

/** What each stage means, in the professor's terms. Never a made-up percentage. */
export const STAGE_LABELS: Record<string, string> = {
  starting: "Starting…",
  reading_files: "Reading your files…",
  reading_material: "Reading your course material…",
  analyzing_previous_exams: "Analysing your previous exams…",
  interpreting: "Interpreting your request…",
  planning: "Planning the assessment…",
  generating_questions: "Writing questions and answer keys…",
  creating_versions: "Creating equivalent versions…",
  reviewing: "Running the AI quality check…",
  finalizing: "Finalizing…",
};

export function stageLabel(stage: string | null | undefined) {
  return (stage && STAGE_LABELS[stage]) || "Working…";
}

const POLL_MS = 1500;

/**
 * Polls a job until it finishes. `onFinish` runs once, with the final run.
 * Pass null to stop following.
 */
export function useRun(runId: string | null, onFinish?: (run: Run) => void) {
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
  const finished = useRef<string | null>(null);
  const callback = useRef(onFinish);
  useEffect(() => {
    callback.current = onFinish;
  });

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    let timeout: ReturnType<typeof setTimeout>;

    async function poll() {
      try {
        const current = await apiRequest<Run>(`/api/runs/${runId}`);
        if (cancelled) return;
        setRun(current);
        setError(null);
        if (current.status !== "running") {
          if (finished.current !== current.id) {
            finished.current = current.id;
            callback.current?.(current);
          }
          return;
        }
      } catch (cause) {
        if (cancelled) return;
        setError(errorMessage(cause));
      }
      timeout = setTimeout(poll, POLL_MS);
    }

    void poll();
    return () => {
      cancelled = true;
      clearTimeout(timeout);
    };
  }, [runId]);

  return { run: runId && run?.id === runId ? run : null, error };
}
