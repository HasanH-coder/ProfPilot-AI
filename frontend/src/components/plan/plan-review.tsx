"use client";

import { AlertTriangle, Check, CircleCheck, FileText, Pencil, RefreshCw, Sparkles, Wand2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useState, type ReactNode } from "react";

import { JobProgress } from "@/components/ai/job-progress";
import { SpecView } from "@/components/plan/spec-view";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, apiRequest, errorMessage } from "@/lib/api/client";
import { useRun } from "@/lib/api/runs";
import type { AiStatus, Exam, Interpretation, StartedRun } from "@/lib/api/types";
import { timeAgo } from "@/lib/time";

type PlanReviewProps = { assessmentId: string };

type Pending = { kind: "interpretation" | "generation"; runId: string } | null;

/**
 * Review screen for the interpreted request: the professor checks the
 * enhanced prompt and ExamSpec, edits or regenerates them, and approves before
 * any exam is generated. Then they choose how to create the exam.
 */
export function PlanReview({ assessmentId }: PlanReviewProps) {
  const router = useRouter();
  const [interpretation, setInterpretation] = useState<Interpretation | null>(null);
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending>(null);
  const [approving, setApproving] = useState(false);
  const base = `/workspace/assessments/${assessmentId}`;

  // Changing this reloads the plan.
  const [reloads, setReloads] = useState(0);
  const load = useCallback(() => setReloads((count) => count + 1), []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiRequest<Interpretation>(`/api/assessments/${assessmentId}/interpretation`),
      apiRequest<AiStatus>(`/api/assessments/${assessmentId}/ai-status`),
    ])
      .then(([nextInterpretation, nextStatus]) => {
        if (cancelled) return;
        setInterpretation(nextInterpretation);
        setStatus(nextStatus);
        setLoadError(null);
        // Pick up a job that is already running (e.g. started before a reload).
        const running = nextStatus.runs.find(
          (run) => run.status === "running" && (run.kind === "interpretation" || run.kind === "generation"),
        );
        if (running) setPending({ kind: running.kind as "interpretation" | "generation", runId: running.id });
      })
      .catch((cause) => {
        if (!cancelled) setLoadError(errorMessage(cause));
      });
    return () => {
      cancelled = true;
    };
  }, [assessmentId, reloads]);

  const { run } = useRun(pending?.runId ?? null, (finished) => {
    const kind = pending?.kind;
    setPending(null);
    if (finished.status === "failed") {
      setActionError(finished.errorMessage ?? "Something went wrong. Please try again.");
      if (kind === "generation") load();
      return;
    }
    if (kind === "generation") router.push(`${base}/exam`);
    else load();
  });

  async function regenerate() {
    setActionError(null);
    try {
      const started = await apiRequest<StartedRun>(`/api/assessments/${assessmentId}/interpretation`, {
        method: "POST",
        freshSession: true,
      });
      setPending({ kind: "interpretation", runId: started.run.id });
    } catch (cause) {
      setActionError(errorMessage(cause));
    }
  }

  async function approve() {
    setActionError(null);
    setApproving(true);
    try {
      setInterpretation(
        await apiRequest<Interpretation>(`/api/assessments/${assessmentId}/interpretation/approval`, { method: "POST" }),
      );
    } catch (cause) {
      setActionError(errorMessage(cause));
      if (cause instanceof ApiError && cause.code === "spec_stale") load();
    } finally {
      setApproving(false);
    }
  }

  async function createExam(mode: "full" | "interactive", replace: boolean) {
    setActionError(null);
    try {
      const result = await apiRequest<{ exam?: Exam; run?: { id: string } }>(`/api/assessments/${assessmentId}/exam`, {
        method: "POST",
        body: { mode, replace },
        freshSession: true,
      });
      if (mode === "interactive") router.push(`${base}/builder`);
      else if (result.run) setPending({ kind: "generation", runId: result.run.id });
    } catch (cause) {
      setActionError(errorMessage(cause));
    }
  }

  if (loadError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>The plan couldn&apos;t be loaded</AlertTitle>
        <AlertDescription>
          {loadError}{" "}
          <button type="button" className="underline underline-offset-4" onClick={() => void load()}>
            Try again
          </button>
        </AlertDescription>
      </Alert>
    );
  }
  if (!interpretation || !status) return <PlanSkeleton />;

  if (pending) {
    return (
      <JobProgress
        run={run}
        startingLabel={pending.kind === "generation" ? "Starting generation…" : "Interpreting your request…"}
      />
    );
  }

  if (interpretation.status === "none" || !interpretation.spec) {
    return (
      <div className="flex flex-col items-start gap-4 rounded-xl border border-dashed p-6">
        <p className="text-sm text-muted-foreground">
          ProfPilot hasn&apos;t interpreted this assessment yet. It reads everything you provided and drafts a plan for
          you to review.
        </p>
        <Button onClick={regenerate}>
          <Sparkles />
          Improve with AI
        </Button>
        {actionError && <ErrorAlert message={actionError} />}
      </div>
    );
  }

  const spec = interpretation.spec;
  const inputs = interpretation.inputs ?? {};
  const stale = interpretation.status === "stale";
  const approved = interpretation.status === "approved";
  const exam = status.exam;

  return (
    <div className="flex flex-col gap-10">
      {stale ? (
        <Alert>
          <AlertTriangle />
          <AlertTitle>This plan is out of date</AlertTitle>
          <AlertDescription>
            {interpretation.staleReasons.join(" ")} Regenerate it so it matches your current setup.
          </AlertDescription>
        </Alert>
      ) : approved ? (
        <Alert>
          <CircleCheck />
          <AlertTitle>Plan approved</AlertTitle>
          <AlertDescription>
            {interpretation.approvedAt ? `Approved ${timeAgo(interpretation.approvedAt)}. ` : ""}
            Choose how to create the exam below. Editing the enhanced instructions will need a new approval.
          </AlertDescription>
        </Alert>
      ) : null}

      <ReviewSection title="Your request">
        <dl className="divide-y rounded-xl border text-sm">
          <RequestRow term="Your instructions">
            {inputs.professor_prompt?.trim() || (
              <span className="text-muted-foreground">
                None written. ProfPilot worked from your settings and files.
              </span>
            )}
          </RequestRow>
          {inputs.additional_notes?.trim() && <RequestRow term="Your notes">{inputs.additional_notes}</RequestRow>}
          <RequestRow term="Files read">
            {inputs.documents && inputs.documents.length > 0 ? (
              <ul className="flex flex-col gap-1">
                {inputs.documents.map((document) => (
                  <li key={document.id} className="flex items-center gap-2">
                    <FileText className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 break-words">{document.name}</span>
                    <Badge variant="outline">{CATEGORY_LABELS[document.category] ?? document.category}</Badge>
                  </li>
                ))}
              </ul>
            ) : (
              <span className="text-muted-foreground">No files. The plan is based on your settings and words only.</span>
            )}
            {inputs.failed_documents && inputs.failed_documents.length > 0 && (
              <p className="mt-2 text-destructive">Couldn&apos;t be read: {inputs.failed_documents.join(", ")}</p>
            )}
          </RequestRow>
          <RequestRow term="Also considered">
            {[
              inputs.style_profile_used ? "your previous exams' style" : null,
              inputs.preferences_used ? "your saved preferences" : null,
            ]
              .filter(Boolean)
              .join(" and ") || <span className="text-muted-foreground">Nothing else</span>}
          </RequestRow>
        </dl>
      </ReviewSection>

      <EnhancedPrompt
        assessmentId={assessmentId}
        interpretation={interpretation}
        onSaved={(next) => setInterpretation(next)}
      />

      <ReviewSection title="Assessment plan" description="What ProfPilot will follow. Each value shows whether it came from you or from the AI.">
        <SpecView spec={spec} />
      </ReviewSection>

      {(spec.assumptions.length > 0 || spec.warnings.length > 0) && (
        <ReviewSection title="Assumptions and warnings">
          <div className="flex flex-col gap-3">
            {spec.warnings.map((warning, index) => (
              <Alert key={`w${index}`}>
                <AlertTriangle />
                <AlertDescription>{warning}</AlertDescription>
              </Alert>
            ))}
            {spec.assumptions.length > 0 && (
              <ul className="list-disc rounded-xl border px-8 py-3 text-sm">
                {spec.assumptions.map((assumption, index) => (
                  <li key={`a${index}`}>{assumption}</li>
                ))}
              </ul>
            )}
          </div>
        </ReviewSection>
      )}

      {actionError && <ErrorAlert message={actionError} />}

      {!approved && (
        <div className="flex flex-wrap gap-2 border-t pt-6">
          <Button onClick={approve} disabled={stale || approving}>
            {approving ? <Spinner /> : <Check />}
            Approve &amp; continue
          </Button>
          <Button variant="outline" onClick={regenerate}>
            <RefreshCw />
            Regenerate
          </Button>
          <Link href={`${base}/edit`} className={buttonVariants({ variant: "outline" })}>
            <Pencil />
            Edit setup
          </Link>
        </div>
      )}

      <ModeChoice
        approved={approved}
        hasExam={Boolean(exam)}
        examPath={exam ? `${base}/${exam.mode === "interactive" && exam.status === "building" ? "builder" : "exam"}` : null}
        onChoose={createExam}
        onRegenerate={regenerate}
      />
    </div>
  );
}

const CATEGORY_LABELS: Record<string, string> = {
  course_material: "Course material",
  previous_exam: "Previous exam",
  additional_attachment: "Attachment",
};

function ReviewSection({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-4">
      <div className="flex flex-col gap-1 border-b pb-3">
        <h2 id={id} className="text-base font-semibold tracking-tight">
          {title}
        </h2>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {children}
    </section>
  );
}

function RequestRow({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 px-4 py-3 sm:grid-cols-[11rem_minmax(0,1fr)] sm:gap-4">
      <dt className="text-muted-foreground">{term}</dt>
      <dd className="min-w-0 break-words whitespace-pre-wrap">{children}</dd>
    </div>
  );
}

function EnhancedPrompt({
  assessmentId,
  interpretation,
  onSaved,
}: {
  assessmentId: string;
  interpretation: Interpretation;
  onSaved: (next: Interpretation) => void;
}) {
  const textareaId = useId();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(interpretation.enhancedPrompt ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      onSaved(
        await apiRequest<Interpretation>(`/api/assessments/${assessmentId}/interpretation`, {
          method: "PATCH",
          body: { enhancedPrompt: text },
        }),
      );
      setEditing(false);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <ReviewSection
      title="AI-enhanced instructions"
      description="A complete brief built from everything you provided. Edit it if anything is off."
    >
      {editing ? (
        <div className="flex flex-col gap-3">
          <label htmlFor={textareaId} className="sr-only">
            AI-enhanced instructions
          </label>
          <Textarea
            id={textareaId}
            className="min-h-56"
            value={text}
            maxLength={8000}
            onChange={(event) => setText(event.target.value)}
          />
          <div className="flex flex-wrap gap-2">
            <Button onClick={save} disabled={saving || !text.trim()}>
              {saving ? <Spinner /> : <Check />}
              Save instructions
            </Button>
            <Button
              variant="outline"
              onClick={() => {
                setText(interpretation.enhancedPrompt ?? "");
                setEditing(false);
              }}
            >
              Cancel
            </Button>
          </div>
          {error && <ErrorAlert message={error} />}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="rounded-xl border bg-card p-4 text-sm leading-relaxed break-words whitespace-pre-wrap">
            {interpretation.enhancedPrompt}
          </p>
          <Button variant="outline" className="w-fit" onClick={() => setEditing(true)}>
            <Pencil />
            Edit instructions
          </Button>
        </div>
      )}
    </ReviewSection>
  );
}

function ModeChoice({
  approved,
  hasExam,
  examPath,
  onChoose,
  onRegenerate,
}: {
  approved: boolean;
  hasExam: boolean;
  examPath: string | null;
  onChoose: (mode: "full" | "interactive", replace: boolean) => Promise<void>;
  onRegenerate: () => void;
}) {
  const [confirming, setConfirming] = useState<"full" | "interactive" | null>(null);
  const [busy, setBusy] = useState<"full" | "interactive" | null>(null);

  async function choose(mode: "full" | "interactive", replace: boolean) {
    setBusy(mode);
    try {
      await onChoose(mode, replace);
    } finally {
      setBusy(null);
    }
  }

  const options = [
    {
      mode: "full" as const,
      icon: Wand2,
      title: "Generate full exam",
      text: "ProfPilot plans and writes the complete assessment with answer keys, runs a quality check, then you review and edit every question.",
      action: "Generate full exam",
    },
    {
      mode: "interactive" as const,
      icon: Sparkles,
      title: "Build with AI",
      text: "Start a live call with ProfPilot or use chat. Watch questions appear in the exam preview, request changes, and approve each question before moving on.",
      action: "Build with AI",
    },
  ];

  return (
    <ReviewSection title="Create the exam" description={approved ? "Choose how you'd like to work. You can edit everything afterwards." : "Review and approve the plan above to unlock these two options."}>
      {hasExam && examPath && (
        <Alert>
          <FileText />
          <AlertDescription>
            This assessment already has an exam.{" "}
            <Link href={examPath} className="font-medium underline underline-offset-4">
              Open it
            </Link>
            , or create a new one below (it replaces the current exam).
          </AlertDescription>
        </Alert>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        {options.map(({ mode, icon: Icon, title, text, action }) => (
          <div key={mode} className="flex flex-col gap-4 rounded-xl border bg-card p-5">
            <div className="flex flex-col gap-2">
              <h3 className="flex items-center gap-2 font-semibold">
                <Icon className="size-4 shrink-0" />
                {title}
              </h3>
              <p className="text-sm text-muted-foreground">{text}</p>
            </div>
            <Button
              className="mt-auto w-fit"
              variant={mode === "full" ? "default" : "outline"}
              disabled={!approved || busy !== null}
              onClick={() => (hasExam ? setConfirming(mode) : void choose(mode, false))}
            >
              {busy === mode ? <Spinner /> : <Icon />}
              {action}
            </Button>
          </div>
        ))}
      </div>
      <p className="text-sm text-muted-foreground">
        Want a different plan?{" "}
        <button type="button" className="underline underline-offset-4" onClick={onRegenerate}>
          Regenerate it
        </button>
        .
      </p>

      <AlertDialog open={confirming !== null} onOpenChange={(open) => !open && setConfirming(null)}>
        <AlertDialogContent className="workspace-theme">
          <AlertDialogHeader>
            <AlertDialogTitle>Replace the current exam?</AlertDialogTitle>
            <AlertDialogDescription>
              The current exam, its questions and your edits to them are deleted and a new exam is created.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                const mode = confirming;
                setConfirming(null);
                if (mode) void choose(mode, true);
              }}
            >
              Replace exam
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </ReviewSection>
  );
}

function ErrorAlert({ message }: { message: string }) {
  return (
    <Alert variant="destructive">
      <AlertDescription>{message}</AlertDescription>
    </Alert>
  );
}

function PlanSkeleton() {
  return (
    <div className="flex flex-col gap-6" aria-busy="true">
      <p role="status" className="sr-only">
        Loading the plan…
      </p>
      <Skeleton className="h-6 w-40" />
      <Skeleton className="h-28 w-full rounded-xl" />
      <Skeleton className="h-6 w-56" />
      <Skeleton className="h-64 w-full rounded-xl" />
    </div>
  );
}
