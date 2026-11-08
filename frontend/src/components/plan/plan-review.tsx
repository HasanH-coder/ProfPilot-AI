"use client";

import { AlertTriangle, ArrowRight, Check, CircleCheck, FileText, Pencil, RefreshCw, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useState, type ReactNode } from "react";

import { JobProgress } from "@/components/ai/job-progress";
import { MathText } from "@/components/exam/question-text";
import { SpecView } from "@/components/plan/spec-view";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, apiRequest, errorMessage } from "@/lib/api/client";
import { useRun } from "@/lib/api/runs";
import type { AiStatus, Interpretation, QuestionPlan, StartedRun } from "@/lib/api/types";
import { timeAgo } from "@/lib/time";

type PlanReviewProps = { assessmentId: string };

type Pending = { runId: string } | null;

/** The question plan, prepared in the background while the professor reads the prompt. */
type PlanProgress = { kind: "unknown" } | { kind: "preparing"; runId: string } | { kind: "ready" };

/** Plan and plan-change jobs run as interpretations, but the prompt stays readable while they run. */
const PLAN_STAGES = new Set(["planning", "changing_plan"]);

/** The question plan is made while the professor reads the prompt, so Next has little left to wait for.
 * One already being made is followed, never started twice (the server refuses a second one too). */
async function preparePlan(assessmentId: string): Promise<PlanProgress> {
  try {
    const plan = await apiRequest<QuestionPlan>(`/api/assessments/${assessmentId}/question-plan`);
    if (plan.status === "ready") return { kind: "ready" };
    // A plan being made is followed through its run; one that stopped early keeps what the professor
    // did with it, and is planned again only from the plan page.
    if (plan.status !== "none") return { kind: "unknown" };
    const started = await apiRequest<StartedRun>(`/api/assessments/${assessmentId}/question-plan`, {
      method: "POST",
      freshSession: true,
    });
    return { kind: "preparing", runId: started.run.id };
  } catch {
    // Nothing is lost: the plan page starts it again (or follows the one running) and reports any problem.
    return { kind: "unknown" };
  }
}

/**
 * Review screen for the interpreted request: the professor checks the
 * enhanced prompt and ExamSpec, edits or regenerates them, then presses Next
 * to build the exam from ProfPilot's question plan (made meanwhile).
 */
export function PlanReview({ assessmentId }: PlanReviewProps) {
  const router = useRouter();
  const [interpretation, setInterpretation] = useState<Interpretation | null>(null);
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending>(null);
  const [planProgress, setPlanProgress] = useState<PlanProgress>({ kind: "unknown" });
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
        // Pick up an interpretation that is already running (e.g. started before a reload).
        const interpreting = nextStatus.runs.filter((run) => run.status === "running" && run.kind === "interpretation");
        const rewriting = interpreting.find((run) => !PLAN_STAGES.has(run.stage ?? ""));
        const planning = interpreting.find((run) => PLAN_STAGES.has(run.stage ?? ""));
        if (rewriting) setPending({ runId: rewriting.id });
        else if (planning) setPlanProgress({ kind: "preparing", runId: planning.id });
        else if (nextInterpretation.status === "draft" || nextInterpretation.status === "approved") {
          // Start the question plan now, while the professor reads the prompt.
          void preparePlan(assessmentId).then((progress) => {
            if (!cancelled) setPlanProgress(progress);
          });
        }
      })
      .catch((cause) => {
        if (!cancelled) setLoadError(errorMessage(cause));
      });
    return () => {
      cancelled = true;
    };
  }, [assessmentId, reloads]);

  const { run } = useRun(pending?.runId ?? null, (finished) => {
    setPending(null);
    if (finished.status === "failed") setActionError(finished.errorMessage ?? "Something went wrong. Please try again.");
    load();
  });

  // Follows the plan being prepared, so the page can say when it is ready.
  useRun(planProgress.kind === "preparing" ? planProgress.runId : null, (finished) => {
    setPlanProgress(finished.status === "succeeded" ? { kind: "ready" } : { kind: "unknown" });
  });

  async function regenerate() {
    setActionError(null);
    try {
      const started = await apiRequest<StartedRun>(`/api/assessments/${assessmentId}/interpretation`, {
        method: "POST",
        freshSession: true,
      });
      setPending({ runId: started.run.id });
    } catch (cause) {
      setActionError(errorMessage(cause));
    }
  }

  /** Confirms the prompt, then opens the question plan, where the exam is built. */
  async function next() {
    setActionError(null);
    setApproving(true);
    try {
      if (interpretation?.status !== "approved") {
        await apiRequest<Interpretation>(`/api/assessments/${assessmentId}/interpretation/approval`, { method: "POST" });
      }
      router.push(`${base}/question-plan`);
    } catch (cause) {
      setActionError(errorMessage(cause));
      if (cause instanceof ApiError && cause.code === "spec_stale") load();
      setApproving(false);
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
      <JobProgress run={run} startingLabel="Interpreting your request…" />
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
            Press Next to go to the question plan and generate the exam there. Editing the improved prompt makes a new plan.
          </AlertDescription>
        </Alert>
      ) : null}

      <ReviewSection title="Your request">
        <dl className="divide-y rounded-xl border text-sm">
          <RequestRow term="Your instructions">
            {(inputs.professor_prompt?.trim() && <MathText text={inputs.professor_prompt} />) || (
              <span className="text-muted-foreground">
                None written. ProfPilot worked from your settings and files.
              </span>
            )}
          </RequestRow>
          {inputs.additional_notes?.trim() && (
            <RequestRow term="Your notes">
              <MathText text={inputs.additional_notes} />
            </RequestRow>
          )}
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
        onSaved={(saved) => {
          setInterpretation(saved);
          // The edit dropped the question plan: reloading starts a new one from the new prompt.
          setPlanProgress({ kind: "unknown" });
          load();
        }}
      />

      <ReviewSection title="Assessment plan" description="What ProfPilot will follow. Each value shows whether it came from you or from the AI.">
        <SpecView spec={spec} editHref={`${base}/edit`} />
      </ReviewSection>

      {(spec.assumptions.length > 0 || spec.warnings.length > 0) && (
        <ReviewSection title="Assumptions and warnings">
          <div className="flex flex-col gap-3">
            {spec.warnings.map((warning, index) => (
              <Alert key={`w${index}`}>
                <AlertTriangle />
                <AlertDescription>
                  <MathText text={warning} />
                </AlertDescription>
              </Alert>
            ))}
            {spec.assumptions.length > 0 && (
              <ul className="list-disc rounded-xl border px-8 py-3 text-sm">
                {spec.assumptions.map((assumption, index) => (
                  <li key={`a${index}`}>
                    <MathText text={assumption} />
                  </li>
                ))}
              </ul>
            )}
          </div>
        </ReviewSection>
      )}

      {exam && (
        <Alert>
          <FileText />
          <AlertDescription>
            <span>
              This assessment already has an exam.{" "}
              <Link href={`${base}/${examPage(exam)}`} className="font-medium underline underline-offset-4">
                Open it
              </Link>
              , or press Next to build a new one from the question plan.
            </span>
          </AlertDescription>
        </Alert>
      )}
      {actionError && <ErrorAlert message={actionError} />}

      <div className="flex flex-wrap items-center gap-2 border-t pt-6">
        <Button onClick={next} disabled={stale || approving}>
          Next: go to plan
          {approving ? <Spinner data-icon="inline-end" /> : <ArrowRight data-icon="inline-end" />}
        </Button>
        <Button variant="outline" onClick={regenerate}>
          <RefreshCw />
          Regenerate
        </Button>
        <Link href={`${base}/edit`} className={buttonVariants({ variant: "outline" })}>
          <Pencil />
          Edit setup
        </Link>
        {!stale && <PlanStatus progress={planProgress} />}
      </div>
    </div>
  );
}

/** Where the question plan is, while the professor reads the prompt. */
function PlanStatus({ progress }: { progress: PlanProgress }) {
  if (progress.kind === "preparing") {
    return (
      <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
        <Spinner />
        Preparing your question plan in the background. Press Next to watch it arrive question by question.
      </p>
    );
  }
  if (progress.kind === "ready") {
    return (
      <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
        <Check className="size-4 text-emerald-600" />
        Your question plan is ready.
      </p>
    );
  }
  return null;
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
            maxLength={12000}
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
          <BriefText text={interpretation.enhancedPrompt ?? ""} />
          <Button variant="outline" className="w-fit" onClick={() => setEditing(true)}>
            <Pencil />
            Edit instructions
          </Button>
        </div>
      )}
    </ReviewSection>
  );
}

// "Assessment objectives: …" — a heading line of the structured brief.
const BRIEF_HEADING = /^([A-Z][A-Za-z'’ -]{2,40}):\s*(.*)$/;

/** The brief, as expandable sections when it is organised under headings (otherwise as written). */
function BriefText({ text }: { text: string }) {
  const sections: { heading: string; body: string[] }[] = [];
  const preamble: string[] = [];
  for (const line of text.split("\n")) {
    const match = BRIEF_HEADING.exec(line.trim());
    if (match) sections.push({ heading: match[1], body: match[2] ? [match[2]] : [] });
    else if (sections.length) sections[sections.length - 1].body.push(line);
    else preamble.push(line);
  }
  if (sections.length < 2) {
    return (
      <p className="rounded-xl border bg-card p-4 text-sm leading-relaxed break-words whitespace-pre-wrap">
        <MathText text={text} />
      </p>
    );
  }
  return (
    <div className="divide-y rounded-xl border bg-card text-sm">
      {preamble.join("\n").trim() && (
        <p className="p-4 leading-relaxed break-words whitespace-pre-wrap">
          <MathText text={preamble.join("\n").trim()} />
        </p>
      )}
      {sections.map(({ heading, body }, index) => (
        <details key={`${heading}-${index}`} open={index < 3} className="group px-4 py-3">
          <summary className="cursor-pointer font-medium marker:text-muted-foreground">{heading}</summary>
          <p className="mt-2 leading-relaxed break-words whitespace-pre-wrap text-muted-foreground">
            <MathText text={body.join("\n").trim()} />
          </p>
        </details>
      ))}
    </div>
  );
}

/** Where an existing exam is opened: still being built from its plan, built in a conversation, or ready. */
export function examPage(exam: { mode: "full" | "interactive"; status: string }): string {
  if (exam.mode === "interactive" && exam.status === "building") return "builder";
  if (exam.mode === "full" && exam.status === "building") return "question-plan";
  return "exam";
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
