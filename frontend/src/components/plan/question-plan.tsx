"use client";

import { AlertTriangle, ArrowRight, Check, MessageSquarePlus, Pencil, PenLine, RefreshCw, Sparkles, Wand2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";

import { JobProgress } from "@/components/ai/job-progress";
import { BloomSwatch } from "@/components/assessment/bloom-chart";
import { DifficultyBar } from "@/components/assessment/difficulty-bar";
import { DIFFICULTY_LABELS, marks, TYPE_LABELS } from "@/components/exam/labels";
import { QuestionCard } from "@/components/exam/question-card";
import { MathText, QuestionText } from "@/components/exam/question-text";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, apiRequest, errorMessage } from "@/lib/api/client";
import { useRun } from "@/lib/api/runs";
import type {
  AiStatus,
  ExamSpec,
  Interpretation,
  PlannedQuestion,
  PlannedTask,
  QuestionDifficulty,
  QuestionPlan,
  QuestionType,
  StartedRun,
} from "@/lib/api/types";
import { BLOOM_LABELS, BLOOM_LEVELS, type BloomLevel } from "@/lib/assessments/draft";

/** A job this page follows besides planning: changing the plan, or writing (one question, or the rest and finishing). */
type Job = { kind: "changing"; runId: string } | { kind: "writing"; runId: string; number: number | null };

/** While questions are being planned or written, each appears as soon as it is saved. */
const REFRESH_MS = 1500;

type Section = NonNullable<QuestionPlan["sections"]>[number];
/** One section of the plan (a case, or a group of questions) with its planned questions. */
type SectionGroup = { index: number; section: Section | null; questions: PlannedQuestion[] };

/** The plan's questions under their sections, in order: a case is shown before its questions. */
function groupBySection(plan: QuestionPlan): SectionGroup[] {
  const sections = plan.sections ?? [];
  if (sections.length === 0) return [{ index: 0, section: null, questions: plan.questions }];
  const groups: SectionGroup[] = sections.map((section, index) => ({ index, section, questions: [] }));
  for (const question of plan.questions) (groups[question.sectionIndex ?? 0] ?? groups[groups.length - 1]).questions.push(question);
  return groups;
}

/**
 * The page where the exam is built: ProfPilot's plan, question by question. The plan appears as it is made
 * (its cases first, then each question as soon as it is planned), and a planned question can be read,
 * changed and generated at once while the rest is still being planned. The professor changes any planned
 * question (by hand, with their own question, or by asking the AI), writes the questions one at a time or
 * all at once, changes a written question, and finishes the exam here.
 */
export function QuestionPlanBuilder({ assessmentId }: { assessmentId: string }) {
  const router = useRouter();
  const [plan, setPlan] = useState<QuestionPlan | null>(null);
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [spec, setSpec] = useState<ExamSpec | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  // The plan being made, followed apart from other jobs: questions can be written while it runs.
  const [planningRunId, setPlanningRunId] = useState<string | null>(null);
  // A write the professor must confirm first, because it replaces an exam made another way.
  const [confirmingReplace, setConfirmingReplace] = useState<{ number: number | null } | null>(null);
  const [confirmingReplan, setConfirmingReplan] = useState(false);
  // Every question is written: the professor keeps reading here, and opens the exam when they choose.
  const [allWritten, setAllWritten] = useState(false);
  // Planning starts by itself when this page opens, once: a failure offers Try again, never a loop.
  const planningStarted = useRef(false);
  const base = `/workspace/assessments/${assessmentId}`;

  const [reloads, setReloads] = useState(0);
  const load = useCallback(() => setReloads((count) => count + 1), []);

  const refreshPlan = useCallback(async () => {
    try {
      setPlan(await apiRequest<QuestionPlan>(`/api/assessments/${assessmentId}/question-plan`));
    } catch {
      // The next refresh or action shows any lasting problem.
    }
  }, [assessmentId]);

  const startPlanning = useCallback(async () => {
    setActionError(null);
    try {
      const started = await apiRequest<StartedRun>(`/api/assessments/${assessmentId}/question-plan`, {
        method: "POST",
        freshSession: true,
      });
      setPlanningRunId(started.run.id);
      // The new plan replaces the old one at once; its questions appear as they are planned.
      void refreshPlan();
    } catch (cause) {
      // Already running (e.g. started from the prompt page): follow that one instead.
      if (cause instanceof ApiError && cause.code === "job_running") load();
      else setActionError(errorMessage(cause));
    }
  }, [assessmentId, load, refreshPlan]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiRequest<QuestionPlan>(`/api/assessments/${assessmentId}/question-plan`),
      apiRequest<AiStatus>(`/api/assessments/${assessmentId}/ai-status`),
      apiRequest<Interpretation>(`/api/assessments/${assessmentId}/interpretation`),
    ])
      .then(([nextPlan, nextStatus, interpretation]) => {
        if (cancelled) return;
        setPlan(nextPlan);
        setStatus(nextStatus);
        setSpec(interpretation.spec);
        setLoadError(null);
        // Pick up jobs already running (e.g. started before a reload, or from the prompt page).
        const running = nextStatus.runs.filter((run) => run.status === "running");
        const planning = running.find((run) => run.kind === "interpretation" && run.stage !== "changing_plan");
        const changing = running.find((run) => run.kind === "interpretation" && run.stage === "changing_plan");
        const writing = running.find((run) => run.kind === "generation");
        if (planning) setPlanningRunId(planning.id);
        if (changing) setJob({ kind: "changing", runId: changing.id });
        else if (writing) setJob({ kind: "writing", runId: writing.id, number: null });
        if (!planning && nextPlan.status === "none" && nextStatus.spec.status === "approved" && !planningStarted.current) {
          planningStarted.current = true;
          void startPlanning();
        }
      })
      .catch((cause) => {
        if (!cancelled) setLoadError(errorMessage(cause));
      });
    return () => {
      cancelled = true;
    };
  }, [assessmentId, reloads, startPlanning]);

  useRun(planningRunId, (finished) => {
    setPlanningRunId(null);
    if (finished.status === "failed") setActionError(finished.errorMessage ?? "ProfPilot couldn't plan the questions. Please try again.");
    void refreshPlan();
  });

  const { run } = useRun(job?.runId ?? null, (finished) => {
    const kind = job?.kind;
    const finishing = job?.kind === "writing" && job.number === null;
    setJob(null);
    if (finished.status === "failed") {
      setActionError(
        finished.errorMessage ??
          (kind === "writing" ? "ProfPilot couldn't write this. Please try again." : "ProfPilot couldn't change the plan. Please try again."),
      );
    }
    void apiRequest<QuestionPlan>(`/api/assessments/${assessmentId}/question-plan`)
      .then((next) => {
        setPlan(next);
        // No jump to the exam page: the professor may be reading a question here.
        if (finishing && finished.status === "succeeded" && next.exam?.status === "ready") setAllWritten(true);
      })
      .catch(() => load());
  });

  // Planned and written questions appear on the page as they are saved.
  const planning = planningRunId !== null || plan?.status === "planning";
  const refreshing = planning || job?.kind === "writing";
  useEffect(() => {
    if (!refreshing) return;
    const timer = setInterval(() => void refreshPlan(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [refreshing, refreshPlan]);

  async function startWriting(number: number | null, replace: boolean) {
    setActionError(null);
    const path =
      number === null
        ? `/api/assessments/${assessmentId}/question-plan/exam`
        : `/api/assessments/${assessmentId}/question-plan/${number}/question`;
    try {
      const started = await apiRequest<StartedRun>(path, { method: "POST", body: { replace }, freshSession: true });
      setAllWritten(false);
      // The whole exam is written on the exam page, where each question appears as soon as it is written.
      if (number === null) {
        router.push(`${base}/exam`);
        return;
      }
      setJob({ kind: "writing", runId: started.run.id, number });
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === "exam_exists") setConfirmingReplace({ number });
      else setActionError(errorMessage(cause));
    }
  }

  async function changePlan(instruction: string): Promise<boolean> {
    setActionError(null);
    try {
      const started = await apiRequest<StartedRun>(`/api/assessments/${assessmentId}/question-plan/changes`, {
        method: "POST",
        body: { instruction },
        freshSession: true,
      });
      setJob({ kind: "changing", runId: started.run.id });
      return true;
    } catch (cause) {
      setActionError(errorMessage(cause));
      return false;
    }
  }

  if (loadError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>The plan couldn&apos;t be loaded</AlertTitle>
        <AlertDescription>
          {loadError}{" "}
          <button type="button" className="underline underline-offset-4" onClick={load}>
            Try again
          </button>
        </AlertDescription>
      </Alert>
    );
  }
  if (!plan || !status) return <PlanSkeleton />;

  if (plan.status === "none" && !planning) {
    const approved = status.spec.status === "approved";
    return (
      <div className="flex flex-col items-start gap-4 rounded-xl border border-dashed p-6">
        <p className="text-sm text-muted-foreground">
          {approved
            ? "ProfPilot hasn't planned the questions yet."
            : "Your prompt changed, so ProfPilot needs you to confirm it before planning the questions."}
        </p>
        {actionError && <ErrorAlert message={actionError} />}
        {approved ? (
          <Button onClick={startPlanning}>
            <RefreshCw />
            Plan the questions
          </Button>
        ) : (
          <Link href={`${base}/plan`} className={buttonVariants()}>
            Confirm your prompt
          </Link>
        )}
      </div>
    );
  }

  const busy = job !== null;
  const complete = plan.status === "ready" && !planning;
  const stopped = plan.status === "stopped" && !planning;
  const written = plan.questions.filter((question) => question.written).length;
  const total = plan.questions.length;
  const outdated = plan.questions.filter((question) => question.outdated).map((question) => question.number);
  const examReady = plan.exam?.status === "ready";
  const finishing = job?.kind === "writing" && job.number === null;
  const remaining = total - written;
  // Generate full exam keeps every question already generated and generates only the rest.
  const finishLabel = remaining === 0 ? "Finish the exam" : "Generate full exam";
  const groups = groupBySection(plan);
  // The section still being planned: its questions are still arriving (the first one, before any question).
  const current = planning ? (plan.questions.at(-1)?.sectionIndex ?? 0) : -1;
  const expected = plan.expectedQuestions ?? null;
  const toPlan = expected !== null ? Math.max(0, expected - total) : null;

  return (
    <div className="flex flex-col gap-8">
      {planning ? (
        <Alert role="status">
          <Spinner />
          <AlertTitle>
            Planning the questions: {total} {expected !== null && expected > total ? `of about ${expected} ` : ""}planned so far
          </AlertTitle>
          <AlertDescription>
            {(plan.sections?.length ?? 0) > 0
              ? "Each case appears first, then each question as soon as it is planned. Read, change or generate a planned question right away; ProfPilot keeps your changes while it plans the rest."
              : "ProfPilot is deciding the sections and cases first. The questions appear here one by one as soon as each is planned."}
          </AlertDescription>
        </Alert>
      ) : (
        <PlanOverview plan={plan} spec={spec} />
      )}

      {stopped && (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>Planning stopped before every question was planned</AlertTitle>
          <AlertDescription>
            The {total} planned {total === 1 ? "question is" : "questions are"} kept, with your changes. Make a new plan to plan the
            whole exam.
          </AlertDescription>
        </Alert>
      )}

      <AskToChange
        disabled={busy || !complete}
        changing={job?.kind === "changing"}
        run={job?.kind === "changing" ? run : null}
        onAsk={changePlan}
      />

      {plan.exam?.status === "failed" && plan.exam.errorMessage && (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>Generation stopped</AlertTitle>
          <AlertDescription>{plan.exam.errorMessage} The questions already generated are kept: try again to continue.</AlertDescription>
        </Alert>
      )}

      {finishing && (
        <Alert role="status">
          <Spinner />
          <AlertTitle>
            Generating the exam: {written} of {total} {total === 1 ? "question" : "questions"} ready
          </AlertTitle>
          <AlertDescription>
            Each question appears under its plan as soon as it is ready. Start reading while the others are being
            generated.
          </AlertDescription>
        </Alert>
      )}
      {allWritten && !finishing && (
        <Alert>
          <Check />
          <AlertTitle>All {total} questions are generated</AlertTitle>
          <AlertDescription>
            <span>
              Any other versions are finishing in the background. Answer keys are written only when you ask.{" "}
              <Link href={`${base}/exam`} className="font-medium underline underline-offset-4">
                Open the exam
              </Link>{" "}
              to review everything and download it.
            </span>
          </AlertDescription>
        </Alert>
      )}

      <div className="flex flex-col gap-8">
        {groups.map((group) => {
          const isCase = Boolean(group.section?.instructions);
          const showTitle = group.section !== null && (isCase || groups.length > 1);
          const waiting = planning && group.questions.length === 0;
          if (!planning && group.questions.length === 0) return null;
          return (
            <section
              key={group.index}
              aria-label={group.section?.title ?? "Questions"}
              className={isCase ? "flex flex-col gap-4 rounded-2xl border p-3 sm:p-4" : "flex flex-col gap-4"}
            >
              {showTitle && group.section && (
                <div className={isCase ? "flex flex-col gap-2 rounded-xl bg-muted/40 p-4 sm:p-5" : "flex flex-col gap-1"}>
                  <h2 className="font-semibold tracking-tight">{group.section.title}</h2>
                  {group.section.instructions && <QuestionText text={group.section.instructions} />}
                </div>
              )}
              <ol className="flex flex-col gap-6">
                {group.questions.map((question) => (
                  <li key={question.number}>
                    <PlannedQuestionCard
                      assessmentId={assessmentId}
                      question={question}
                      disabled={busy}
                      writing={job?.kind === "writing" && job.number === question.number}
                      pending={
                        (job?.kind === "writing" && job.number === question.number) || (finishing && (!question.written || question.outdated))
                      }
                      onSaved={setPlan}
                      onWrite={() => void startWriting(question.number, false)}
                      onChanged={() => void refreshPlan()}
                    />
                  </li>
                ))}
              </ol>
              {waiting && group.index !== current && (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Spinner />
                  {isCase ? "The questions on this case are planned next." : "This section's questions are planned next."}
                </p>
              )}
              {planning && group.index === current && <PlanningPlaceholders next={total + 1} more={toPlan} />}
            </section>
          );
        })}
      </div>

      {actionError && <ErrorAlert message={actionError} />}

      <section aria-labelledby="finish-heading" className="flex flex-col gap-4 rounded-xl border bg-card p-4 sm:p-5">
        <div className="flex flex-col gap-1">
          <h2 id="finish-heading" className="font-semibold tracking-tight">
            {written} of {total} questions generated
          </h2>
          <p className="text-sm text-muted-foreground">
            {!complete
              ? "Generate any planned question above now. Generate full exam is available once every question is planned."
              : remaining === 0
                ? "Every question is generated. Finish the exam to create the other versions. Answer keys are written only when you ask."
                : `Generate questions one at a time above, or press Generate full exam to generate the ${remaining} remaining ${
                    remaining === 1 ? "question" : "questions"
                  }${written > 0 ? `, keeping the ${written} you already generated` : ""}${
                    outdated.length > 0 ? ` (it also regenerates ${listNumbers(outdated)}, whose plan you changed)` : ""
                  }. The exam page opens and shows each question, in plan order, as soon as it is ready. Only the questions are written: generate their answer keys afterwards, one at a time or all at once next to the download.`}
          </p>
        </div>
        {finishing && (
          <JobProgress run={run} startingLabel="Generating the exam…" note="Questions appear above as they are generated. You can leave this page." />
        )}
        <div className="flex flex-wrap gap-2">
          <Button size="lg" disabled={busy || !complete} onClick={() => void startWriting(null, false)}>
            {finishing ? <Spinner /> : <Wand2 />}
            {finishLabel}
          </Button>
          {examReady && (
            <Link href={`${base}/exam`} className={buttonVariants({ size: "lg", variant: "outline" })}>
              Open the finished exam
              <ArrowRight data-icon="inline-end" />
            </Link>
          )}
          <Button size="lg" variant="ghost" disabled={busy || planning} onClick={() => setConfirmingReplan(true)}>
            <RefreshCw />
            Make a new plan
          </Button>
        </div>
      </section>

      <BuildWithAiInstead assessmentId={assessmentId} disabled={busy || planning} />

      <AlertDialog open={confirmingReplace !== null} onOpenChange={(open) => !open && setConfirmingReplace(null)}>
        <AlertDialogContent className="workspace-theme">
          <AlertDialogHeader>
            <AlertDialogTitle>Replace the current exam?</AlertDialogTitle>
            <AlertDialogDescription>
              This assessment already has an exam that wasn&apos;t written from this plan. It is deleted, with its questions and
              your edits to them, and the questions you write here make the new exam.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                const number = confirmingReplace?.number ?? null;
                setConfirmingReplace(null);
                void startWriting(number, true);
              }}
            >
              Replace exam
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={confirmingReplan} onOpenChange={setConfirmingReplan}>
        <AlertDialogContent className="workspace-theme">
          <AlertDialogHeader>
            <AlertDialogTitle>Make a new plan?</AlertDialogTitle>
            <AlertDialogDescription>
              ProfPilot plans every question again from your prompt. Your changes to this plan are lost, and questions you
              already wrote stay in the current exam until you write from the new plan (which replaces it).
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirmingReplan(false);
                void startPlanning();
              }}
            >
              Make a new plan
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/** Where the next questions will appear while they are being planned. */
function PlanningPlaceholders({ next, more }: { next: number; more: number | null }) {
  const shown = Math.max(1, Math.min(3, more ?? 1));
  return (
    <div className="flex flex-col gap-3" aria-busy="true">
      <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
        <Spinner />
        Planning question {next}…{more !== null && more > 1 ? ` About ${more} more to plan.` : ""}
      </p>
      {Array.from({ length: shown }, (_, index) => (
        <Skeleton key={index} className="h-24 w-full rounded-xl" />
      ))}
    </div>
  );
}

/** The other way to make the exam, offered after the plan: question by question, in a call or chat. */
function BuildWithAiInstead({ assessmentId, disabled }: { assessmentId: string; disabled: boolean }) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start(replace: boolean) {
    setBusy(true);
    setError(null);
    try {
      await apiRequest(`/api/assessments/${assessmentId}/exam`, {
        method: "POST",
        body: { mode: "interactive", replace },
        freshSession: true,
      });
      router.push(`/workspace/assessments/${assessmentId}/builder`);
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === "exam_exists") setConfirming(true);
      else setError(errorMessage(cause));
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="build-with-ai-heading" className="flex flex-col gap-3 rounded-xl border border-dashed p-4 sm:p-5">
      <div className="flex flex-col gap-1">
        <h2 id="build-with-ai-heading" className="flex items-center gap-2 font-semibold tracking-tight">
          <Sparkles className="size-4 shrink-0" />
          Prefer a conversation?
        </h2>
        <p className="text-sm text-muted-foreground">
          Instead of this plan, build the exam question by question in a live call or chat with ProfPilot.
        </p>
      </div>
      {error && <ErrorAlert message={error} />}
      <Button className="w-fit" variant="outline" disabled={disabled || busy} onClick={() => void start(false)}>
        {busy ? <Spinner /> : <Sparkles />}
        Build with AI
      </Button>
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent className="workspace-theme">
          <AlertDialogHeader>
            <AlertDialogTitle>Replace the current exam?</AlertDialogTitle>
            <AlertDialogDescription>
              The current exam, its questions and your edits to them are deleted, and you build a new one with ProfPilot.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                setConfirming(false);
                void start(true);
              }}
            >
              Replace exam
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}

function listNumbers(numbers: number[]) {
  const names = numbers.map((number) => `question ${number}`);
  return names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}

/** The plan at a glance: size, time, the splits against the professor's targets, and the planner's warnings. */
function PlanOverview({ plan, spec }: { plan: QuestionPlan; spec: ExamSpec | null }) {
  const summary = plan.summary;
  return (
    <section aria-labelledby="plan-summary" className="flex flex-col gap-4 rounded-xl border bg-card p-4 sm:p-5">
      <div className="flex flex-col gap-2">
        <h2 id="plan-summary" className="font-semibold tracking-tight">
          {plan.questions.length} {plan.questions.length === 1 ? "question" : "questions"}
          {summary && ` · ${marks(summary.totalPoints)} · about ${Math.round(summary.estimatedMinutes)} min`}
        </h2>
        {plan.rationale && (
          <p className="text-sm leading-relaxed text-muted-foreground">
            <MathText text={plan.rationale} />
          </p>
        )}
      </div>
      {summary && (
        <dl className="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
          <Target term="Total marks" value={String(summary.totalPoints)} target={spec?.total_points ? String(spec.total_points) : null} />
          <Target
            term="Answering time"
            value={`~${Math.round(summary.estimatedMinutes)} min`}
            target={spec?.duration_minutes ? `${spec.duration_minutes} min exam` : null}
          />
          <Target
            term="Multiple choice"
            value={`${summary.mcqPercent}% of marks`}
            target={spec?.question_format ? `${spec.question_format.mcq_percent}%` : null}
          />
        </dl>
      )}
      {summary && (
        <div className="flex flex-col gap-1.5 text-sm">
          <p className="text-muted-foreground">Difficulty by marks</p>
          <DifficultyBar percentages={{ easy: summary.easyPercent, medium: summary.mediumPercent, hard: summary.hardPercent }} />
          <p className="text-xs text-muted-foreground">
            {summary.easyPercent}% easy · {summary.mediumPercent}% medium · {summary.hardPercent}% hard
            {spec?.difficulty && ` (target ${spec.difficulty.easy_percent}/${spec.difficulty.medium_percent}/${spec.difficulty.hard_percent})`}
          </p>
        </div>
      )}
      {plan.tasks && <TaskMix tasks={plan.tasks} previous={plan.previousTasks ?? null} />}
      {summary?.bloom && (
        <div className="flex flex-col gap-1.5 text-sm">
          <p className="text-muted-foreground">Bloom&apos;s levels by marks</p>
          <ul className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs sm:grid-cols-3">
            {BLOOM_LEVELS.map((level) => (
              <li key={level} className="flex items-center gap-1.5">
                <BloomSwatch level={level} />
                <span className="flex-1">{BLOOM_LABELS[level]}</span>
                <span className="tabular-nums">
                  {summary.bloom?.[level]}%
                  {spec?.bloom && <span className="text-muted-foreground"> / {spec.bloom[`${level}_percent`]}%</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {(plan.distributionWarnings.length > 0 || plan.warnings.length > 0) && (
        <ul className="flex flex-col gap-1.5 text-sm">
          {[...plan.distributionWarnings.map((warning) => warning.message), ...plan.warnings].map((message, index) => (
            <li key={index} className="flex items-start gap-1.5 text-amber-700 dark:text-amber-400">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              <MathText text={message} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const TASK_LABELS: Record<PlannedTask, string> = {
  conceptual: "Conceptual",
  application: "Application",
  calculation: "Calculation",
};
const TASK_ORDER: PlannedTask[] = ["conceptual", "application", "calculation"];

/** What students do across the plan, next to the same counts in the professor's previous exams. */
function TaskMix({ tasks, previous }: { tasks: Record<PlannedTask, number>; previous: Record<PlannedTask, number> | null }) {
  const previousTotal = previous ? TASK_ORDER.reduce((sum, task) => sum + previous[task], 0) : 0;
  return (
    <div className="flex flex-col gap-1 text-sm">
      <p className="text-muted-foreground">What students do</p>
      <p>
        {TASK_ORDER.map((task) => `${tasks[task]} ${TASK_LABELS[task].toLowerCase()}`).join(" · ")}
        {previous && previousTotal > 0 && (
          <span className="text-muted-foreground">
            {" "}
            · your previous exams: {TASK_ORDER.map((task) => `${previous[task]} ${TASK_LABELS[task].toLowerCase()}`).join(" · ")} (of{" "}
            {previousTotal})
          </span>
        )}
      </p>
    </div>
  );
}

function Target({ term, value, target }: { term: string; value: string; target: string | null }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-muted-foreground">{term}</dt>
      <dd className="text-right">
        {value}
        {target && <span className="text-muted-foreground"> · target {target}</span>}
      </dd>
    </div>
  );
}

/** "Tell the AI what to change": the AI changes the plan as asked and keeps everything else. */
function AskToChange({
  disabled,
  changing,
  run,
  onAsk,
}: {
  disabled: boolean;
  changing: boolean;
  run: Parameters<typeof JobProgress>[0]["run"];
  onAsk: (instruction: string) => Promise<boolean>;
}) {
  const id = useId();
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const instruction = text.trim();
    if (!instruction) return;
    setSending(true);
    if (await onAsk(instruction)) setText("");
    setSending(false);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2 rounded-xl border bg-card p-4 sm:p-5">
      <Label htmlFor={id} className="font-semibold">
        Tell ProfPilot what to change in the plan
      </Label>
      <Textarea
        id={id}
        maxLength={2000}
        placeholder="For example: make question 3 harder, add a question on Lecture 4, or replace question 5 with one about load balancing."
        value={text}
        disabled={disabled}
        onChange={(event) => setText(event.target.value)}
      />
      {changing ? (
        <JobProgress run={run} startingLabel="Changing the plan…" note="ProfPilot changes what you asked and keeps the rest." />
      ) : (
        <Button type="submit" className="w-fit" disabled={disabled || sending || !text.trim()}>
          {sending ? <Spinner /> : <Wand2 />}
          Change the plan
        </Button>
      )}
    </form>
  );
}

type EditState = {
  type: QuestionType;
  difficulty: QuestionDifficulty;
  cognitiveLevel: BloomLevel;
  points: string;
  topic: string;
  assessmentTarget: string;
  professorQuestion: string;
};

function editState(question: PlannedQuestion): EditState {
  return {
    type: question.type,
    difficulty: question.difficulty,
    cognitiveLevel: question.cognitiveLevel,
    points: String(question.points),
    topic: question.topic,
    assessmentTarget: question.assessmentTarget,
    professorQuestion: question.professorQuestion,
  };
}

/** One planned question: its plan (to read and change), and the question once it is written. */
function PlannedQuestionCard({
  assessmentId,
  question,
  disabled,
  writing,
  pending,
  onSaved,
  onWrite,
  onChanged,
}: {
  assessmentId: string;
  question: PlannedQuestion;
  disabled: boolean;
  writing: boolean;
  /** Being written now: shown where the question will appear. */
  pending: boolean;
  onSaved: (plan: QuestionPlan) => void;
  onWrite: () => void;
  onChanged: () => void;
}) {
  const ids = { type: useId(), difficulty: useId(), level: useId(), points: useId(), topic: useId(), target: useId(), own: useId() };
  const headingId = useId();
  const [editing, setEditing] = useState<"plan" | "own" | null>(null);
  const [state, setState] = useState<EditState>(() => editState(question));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof EditState>(key: K, value: EditState[K]) => setState((current) => ({ ...current, [key]: value }));

  function open(mode: "plan" | "own") {
    setState(editState(question));
    setError(null);
    setEditing(mode);
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const saved = await apiRequest<QuestionPlan>(`/api/assessments/${assessmentId}/question-plan/${question.number}`, {
        method: "PATCH",
        body: { ...state, points: Number(state.points) },
      });
      setEditing(null);
      onSaved(saved);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <article aria-labelledby={headingId} className="flex flex-col gap-3 rounded-xl border border-dashed bg-muted/30 p-4 sm:p-5">
        <div className="flex flex-wrap items-center gap-2">
          <h3 id={headingId} className="font-semibold">
            Question {question.number}: {question.professorQuestion ? "your own question" : <MathText text={question.topic} />}
          </h3>
          <Badge variant="secondary">{TYPE_LABELS[question.type]}</Badge>
          <Badge variant="outline">{DIFFICULTY_LABELS[question.difficulty]}</Badge>
          <Badge variant="outline">{BLOOM_LABELS[question.cognitiveLevel]}</Badge>
          {question.task && <Badge variant="outline">{TASK_LABELS[question.task]}</Badge>}
          <span className="text-xs text-muted-foreground">
            {marks(question.points)} · about {question.estimatedMinutes} min
          </span>
          {question.outdated && (
            <Badge variant="outline" className="border-amber-500 text-amber-700 dark:text-amber-400">
              Plan changed since it was written
            </Badge>
          )}
        </div>

        {editing === null && (
          <>
            {question.professorQuestion ? (
              <div className="flex flex-col gap-1 text-sm">
                <p className="font-medium">Your own question</p>
                <p className="break-words whitespace-pre-wrap">
                  <MathText text={question.professorQuestion} />
                </p>
              </div>
            ) : (
              <div className="flex flex-col gap-1 text-sm">
                {question.assessmentTarget && (
                  <p className="text-muted-foreground">
                    Students will: <MathText text={question.assessmentTarget} />
                  </p>
                )}
                {question.sourceHint && <p className="text-xs text-muted-foreground">From {question.sourceHint}</p>}
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant={question.written && !question.outdated ? "outline" : "default"} disabled={disabled} onClick={onWrite}>
                {writing ? <Spinner /> : <PenLine />}
                {writing ? "Generating…" : question.written ? "Regenerate question" : "Generate question"}
              </Button>
              <Button variant="outline" size="sm" disabled={disabled} onClick={() => open("plan")}>
                <Pencil />
                Edit plan
              </Button>
              <Button variant="outline" size="sm" disabled={disabled} onClick={() => open("own")}>
                <MessageSquarePlus />
                {question.professorQuestion ? "Change your question" : "Give your own question"}
              </Button>
            </div>
          </>
        )}

        {editing !== null && (
          <div className="flex flex-col gap-3">
            {editing === "plan" ? (
              <>
                <div className="grid gap-3 sm:grid-cols-4">
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor={ids.type}>Type</Label>
                    <Select items={TYPE_LABELS} value={state.type} onValueChange={(value) => value && set("type", value as QuestionType)}>
                      <SelectTrigger id={ids.type} className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent className="workspace-theme">
                        {Object.entries(TYPE_LABELS).map(([value, label]) => (
                          <SelectItem key={value} value={value}>
                            {label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor={ids.difficulty}>Difficulty</Label>
                    <Select
                      items={DIFFICULTY_LABELS}
                      value={state.difficulty}
                      onValueChange={(value) => value && set("difficulty", value as QuestionDifficulty)}
                    >
                      <SelectTrigger id={ids.difficulty} className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent className="workspace-theme">
                        {Object.entries(DIFFICULTY_LABELS).map(([value, label]) => (
                          <SelectItem key={value} value={value}>
                            {label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor={ids.level}>Bloom&apos;s level</Label>
                    <Select
                      items={BLOOM_LABELS}
                      value={state.cognitiveLevel}
                      onValueChange={(value) => value && set("cognitiveLevel", value as BloomLevel)}
                    >
                      <SelectTrigger id={ids.level} className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent className="workspace-theme">
                        {BLOOM_LEVELS.map((level) => (
                          <SelectItem key={level} value={level}>
                            {BLOOM_LABELS[level]}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor={ids.points}>Marks</Label>
                    <Input
                      id={ids.points}
                      type="number"
                      inputMode="decimal"
                      min={0.5}
                      step={0.5}
                      value={state.points}
                      onChange={(event) => set("points", event.target.value)}
                    />
                  </div>
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor={ids.topic}>Topic</Label>
                  <Input id={ids.topic} autoFocus maxLength={300} value={state.topic} onChange={(event) => set("topic", event.target.value)} />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor={ids.target}>What students should do</Label>
                  <Textarea
                    id={ids.target}
                    maxLength={1000}
                    value={state.assessmentTarget}
                    onChange={(event) => set("assessmentTarget", event.target.value)}
                  />
                </div>
              </>
            ) : (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={ids.own}>Your own question</Label>
                <Textarea
                  id={ids.own}
                  autoFocus
                  className="min-h-28"
                  maxLength={4000}
                  placeholder="Write the question. ProfPilot keeps your wording and adds what it needs, such as choices."
                  value={state.professorQuestion}
                  onChange={(event) => set("professorQuestion", event.target.value)}
                />
              </div>
            )}
            {question.written && (
              <p className="text-xs text-muted-foreground">
                This question is already generated. Saving keeps it as it is; regenerate it afterwards to follow the new plan.
              </p>
            )}
            {error && <ErrorAlert message={error} />}
            <div className="flex flex-wrap gap-2">
              <Button size="sm" onClick={save} disabled={saving || !state.topic.trim()}>
                {saving ? <Spinner /> : <Check />}
                Save
              </Button>
              <Button variant="outline" size="sm" onClick={() => setEditing(null)} disabled={saving}>
                Cancel
              </Button>
            </div>
          </div>
        )}
      </article>

      {question.written && !(pending && question.outdated) ? (
        <QuestionCard question={question.written} headingLevel={3} showKey multiVersion={false} onChanged={onChanged} />
      ) : (
        pending && (
          <div
            role="status"
            className="flex items-center gap-2 rounded-xl border border-dashed bg-card p-4 text-sm text-muted-foreground sm:p-5"
          >
            <Spinner />
            Generating question {question.number}…
          </div>
        )
      )}
    </div>
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
    <div className="flex flex-col gap-4" aria-busy="true">
      <p role="status" className="sr-only">
        Loading the plan…
      </p>
      <Skeleton className="h-40 w-full rounded-xl" />
      <Skeleton className="h-28 w-full rounded-xl" />
      <Skeleton className="h-32 w-full rounded-xl" />
      <Skeleton className="h-32 w-full rounded-xl" />
    </div>
  );
}
