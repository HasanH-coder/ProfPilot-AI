"use client";

import { AlertTriangle, ArrowRight, Check, MessageSquarePlus, Pencil, PenLine, RefreshCw, Wand2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";

import { JobProgress } from "@/components/ai/job-progress";
import { BloomSwatch } from "@/components/assessment/bloom-chart";
import { DifficultyBar } from "@/components/assessment/difficulty-bar";
import { DIFFICULTY_LABELS, marks, TYPE_LABELS } from "@/components/exam/labels";
import { QuestionCard } from "@/components/exam/question-card";
import { MathText } from "@/components/exam/question-text";
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
  QuestionDifficulty,
  QuestionPlan,
  QuestionType,
  StartedRun,
} from "@/lib/api/types";
import { BLOOM_LABELS, BLOOM_LEVELS, type BloomLevel } from "@/lib/assessments/draft";

/** A job this page follows: making or changing the plan, or writing (one question, or the rest and finishing). */
type Job =
  | { kind: "planning" | "changing"; runId: string }
  | { kind: "writing"; runId: string; number: number | null };

/** While the exam is being finished, the written questions appear as they come. */
const REFRESH_WHILE_WRITING_MS = 4000;

/**
 * The page where the exam is built: ProfPilot's plan, question by question. The professor reads each
 * planned question, changes it (by hand, with their own question, or by asking the AI), writes the
 * questions one at a time or all at once, changes a written question, and finishes the exam here.
 */
export function QuestionPlanBuilder({ assessmentId }: { assessmentId: string }) {
  const router = useRouter();
  const [plan, setPlan] = useState<QuestionPlan | null>(null);
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [spec, setSpec] = useState<ExamSpec | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  // A write the professor must confirm first, because it replaces an exam made another way.
  const [confirmingReplace, setConfirmingReplace] = useState<{ number: number | null } | null>(null);
  const [confirmingReplan, setConfirmingReplan] = useState(false);
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
      setJob({ kind: "planning", runId: started.run.id });
    } catch (cause) {
      // Already running (e.g. started from the prompt page): follow that one instead.
      if (cause instanceof ApiError && cause.code === "job_running") load();
      else setActionError(errorMessage(cause));
    }
  }, [assessmentId, load]);

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
        // Pick up a job that is already running (e.g. started before a reload, or from the prompt page).
        const running = nextStatus.runs.filter((run) => run.status === "running");
        const planning = running.find((run) => run.kind === "interpretation");
        const writing = running.find((run) => run.kind === "generation");
        if (planning) setJob({ kind: planning.stage === "changing_plan" ? "changing" : "planning", runId: planning.id });
        else if (writing) setJob({ kind: "writing", runId: writing.id, number: null });
        else if (nextPlan.status === "none" && nextStatus.spec.status === "approved" && !planningStarted.current) {
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

  const { run } = useRun(job?.runId ?? null, (finished) => {
    const kind = job?.kind;
    setJob(null);
    if (finished.status === "failed") {
      setActionError(
        finished.errorMessage ??
          (kind === "writing" ? "ProfPilot couldn't write this. Please try again." : "ProfPilot couldn't plan the questions. Please try again."),
      );
    }
    void apiRequest<QuestionPlan>(`/api/assessments/${assessmentId}/question-plan`)
      .then((next) => {
        setPlan(next);
        // Finishing ends on the exam page, with every version, the answer keys and the quality check.
        if (kind === "writing" && finished.status === "succeeded" && next.exam?.status === "ready") router.push(`${base}/exam`);
      })
      .catch(() => load());
  });

  // Written questions appear on the page as they are written.
  const writing = job?.kind === "writing";
  useEffect(() => {
    if (!writing) return;
    const timer = setInterval(() => void refreshPlan(), REFRESH_WHILE_WRITING_MS);
    return () => clearInterval(timer);
  }, [writing, refreshPlan]);

  async function startWriting(number: number | null, replace: boolean) {
    setActionError(null);
    const path =
      number === null
        ? `/api/assessments/${assessmentId}/question-plan/exam`
        : `/api/assessments/${assessmentId}/question-plan/${number}/question`;
    try {
      const started = await apiRequest<StartedRun>(path, { method: "POST", body: { replace }, freshSession: true });
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

  if (job?.kind === "planning") {
    return (
      <JobProgress
        run={run}
        startingLabel="Planning the assessment…"
        note="ProfPilot is deciding what to ask, question by question. This can take a minute or two."
      />
    );
  }

  if (plan.status === "none") {
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
  const written = plan.questions.filter((question) => question.written).length;
  const total = plan.questions.length;
  const outdated = plan.questions.filter((question) => question.outdated).map((question) => question.number);
  const examReady = plan.exam?.status === "ready";
  const finishLabel = written === 0 ? "Write the whole exam" : written === total ? "Finish the exam" : `Write the other ${total - written} and finish`;

  return (
    <div className="flex flex-col gap-8">
      <PlanOverview plan={plan} spec={spec} />

      <AskToChange disabled={busy} changing={job?.kind === "changing"} run={job?.kind === "changing" ? run : null} onAsk={changePlan} />

      {plan.exam?.status === "failed" && plan.exam.errorMessage && (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>Writing stopped</AlertTitle>
          <AlertDescription>{plan.exam.errorMessage} The questions already written are kept: try again to continue.</AlertDescription>
        </Alert>
      )}

      <ol className="flex flex-col gap-6" aria-label="Planned questions">
        {plan.questions.map((question) => (
          <li key={question.number}>
            <PlannedQuestionCard
              assessmentId={assessmentId}
              question={question}
              disabled={busy}
              writing={job?.kind === "writing" && job.number === question.number}
              onSaved={setPlan}
              onWrite={() => void startWriting(question.number, false)}
              onChanged={() => void refreshPlan()}
            />
          </li>
        ))}
      </ol>

      {actionError && <ErrorAlert message={actionError} />}

      <section aria-labelledby="finish-heading" className="flex flex-col gap-4 rounded-xl border bg-card p-4 sm:p-5">
        <div className="flex flex-col gap-1">
          <h2 id="finish-heading" className="font-semibold tracking-tight">
            {written} of {total} questions written
          </h2>
          <p className="text-sm text-muted-foreground">
            Finishing writes every question still missing
            {outdated.length > 0 ? ` and rewrites ${listNumbers(outdated)}, whose plan you changed,` : ""} then creates the
            other versions and runs the quality check. You then see the whole exam with its answer keys, ready to download.
          </p>
        </div>
        {job?.kind === "writing" && job.number === null && (
          <JobProgress run={run} startingLabel="Writing the exam…" note="Questions appear above as they are written. You can leave this page." />
        )}
        <div className="flex flex-wrap gap-2">
          <Button size="lg" disabled={busy} onClick={() => void startWriting(null, false)}>
            {job?.kind === "writing" && job.number === null ? <Spinner /> : <Wand2 />}
            {finishLabel}
          </Button>
          {examReady && (
            <Link href={`${base}/exam`} className={buttonVariants({ size: "lg", variant: "outline" })}>
              Open the finished exam
              <ArrowRight data-icon="inline-end" />
            </Link>
          )}
          <Button size="lg" variant="ghost" disabled={busy} onClick={() => setConfirmingReplan(true)}>
            <RefreshCw />
            Make a new plan
          </Button>
        </div>
      </section>

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
  onSaved,
  onWrite,
  onChanged,
}: {
  assessmentId: string;
  question: PlannedQuestion;
  disabled: boolean;
  writing: boolean;
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
            Plan for question {question.number}
          </h3>
          <Badge variant="secondary">{TYPE_LABELS[question.type]}</Badge>
          <Badge variant="outline">{DIFFICULTY_LABELS[question.difficulty]}</Badge>
          <Badge variant="outline">{BLOOM_LABELS[question.cognitiveLevel]}</Badge>
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
                <p>
                  One question on{" "}
                  <strong className="font-medium">
                    <MathText text={question.topic} />
                  </strong>
                  .
                </p>
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
                {writing ? "Writing…" : question.written ? "Rewrite this question" : "Write this question"}
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
                  placeholder="Write the question. ProfPilot keeps your wording and adds what it needs, such as choices and the answer key."
                  value={state.professorQuestion}
                  onChange={(event) => set("professorQuestion", event.target.value)}
                />
              </div>
            )}
            {question.written && (
              <p className="text-xs text-muted-foreground">
                This question is already written. Saving keeps it as it is; rewrite it afterwards to follow the new plan.
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

      {question.written && (
        <QuestionCard question={question.written} headingLevel={3} showKey multiVersion={false} onChanged={onChanged} />
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
