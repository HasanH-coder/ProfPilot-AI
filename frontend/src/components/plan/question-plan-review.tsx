"use client";

import { Check, MessageSquarePlus, Pencil, RefreshCw, Sparkles, Wand2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { JobProgress } from "@/components/ai/job-progress";
import { DIFFICULTY_LABELS, marks, TYPE_LABELS } from "@/components/exam/labels";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, apiRequest, errorMessage } from "@/lib/api/client";
import { useRun } from "@/lib/api/runs";
import type { AiStatus, PlannedQuestion, QuestionPlan, StartedRun, WrittenQuestion } from "@/lib/api/types";
import { BLOOM_LABELS } from "@/lib/assessments/draft";
import { cn } from "@/lib/utils";

/**
 * ProfPilot's plan, question by question, before anything is written: the
 * professor changes any question or gives their own, then generates the exam.
 */
export function QuestionPlanReview({ assessmentId }: { assessmentId: string }) {
  const router = useRouter();
  const [plan, setPlan] = useState<QuestionPlan | null>(null);
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [planningRun, setPlanningRun] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [confirmingReplace, setConfirmingReplace] = useState(false);
  // How many questions are being generated on this page: the exam waits for them, so it keeps them.
  const [writingCount, setWritingCount] = useState(0);
  // Planning starts by itself when this page opens, once: a failure offers Try again, never a loop.
  const planningStarted = useRef(false);
  const base = `/workspace/assessments/${assessmentId}`;

  // Changing this reloads the plan.
  const [reloads, setReloads] = useState(0);
  const load = useCallback(() => setReloads((count) => count + 1), []);

  const startPlanning = useCallback(async () => {
    setActionError(null);
    try {
      const started = await apiRequest<StartedRun>(`/api/assessments/${assessmentId}/question-plan`, {
        method: "POST",
        freshSession: true,
      });
      setPlanningRun(started.run.id);
    } catch (cause) {
      // Already running (e.g. started in another tab): follow that one instead.
      if (cause instanceof ApiError && cause.code === "job_running") load();
      else setActionError(errorMessage(cause));
    }
  }, [assessmentId, load]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiRequest<QuestionPlan>(`/api/assessments/${assessmentId}/question-plan`),
      apiRequest<AiStatus>(`/api/assessments/${assessmentId}/ai-status`),
    ])
      .then(([nextPlan, nextStatus]) => {
        if (cancelled) return;
        setPlan(nextPlan);
        setStatus(nextStatus);
        setLoadError(null);
        const running = nextStatus.runs.find((run) => run.kind === "interpretation" && run.status === "running");
        if (running) setPlanningRun(running.id);
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

  const { run } = useRun(planningRun, (finished) => {
    if (finished.status === "failed") {
      setPlanningRun(null);
      setActionError(finished.errorMessage ?? "ProfPilot couldn't plan the questions. Please try again.");
      load();
      return;
    }
    // Done: keep showing the progress until the finished plan is here. Clearing it first would
    // show the old, empty plan ("hasn't planned the questions yet - Try again") until the reload ends.
    apiRequest<QuestionPlan>(`/api/assessments/${assessmentId}/question-plan`)
      .then(setPlan)
      .catch(() => {})
      .finally(() => {
        setPlanningRun(null);
        load();
      });
  });

  async function generate(replace: boolean) {
    setActionError(null);
    setGenerating(true);
    try {
      await apiRequest(`/api/assessments/${assessmentId}/exam`, {
        method: "POST",
        body: { mode: "full", replace },
        freshSession: true,
      });
      // The exam page follows the generation and shows where it is.
      router.push(`${base}/exam`);
    } catch (cause) {
      setActionError(errorMessage(cause));
      setGenerating(false);
    }
  }

  function planAgain() {
    if (window.confirm("Make a new plan? Your changes to this one will be replaced.")) void startPlanning();
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

  if (planningRun) {
    return (
      <JobProgress
        run={run}
        startingLabel="Planning the assessment…"
        note="ProfPilot is deciding what to ask. This can take a minute or two."
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
            Try again
          </Button>
        ) : (
          <Link href={`${base}/plan`} className={buttonVariants()}>
            Confirm your prompt
          </Link>
        )}
      </div>
    );
  }

  const totalMinutes = plan.questions.reduce((total, question) => total + question.estimatedMinutes, 0);
  const totalPoints = plan.questions.reduce((total, question) => total + question.points, 0);
  const hasExam = Boolean(status.exam);

  return (
    <div className="flex flex-col gap-8">
      <section aria-labelledby="plan-summary" className="flex flex-col gap-2 rounded-xl border bg-card p-4 sm:p-5">
        <h2 id="plan-summary" className="font-semibold tracking-tight">
          {plan.questions.length} {plan.questions.length === 1 ? "question" : "questions"} · {marks(totalPoints)} · about{" "}
          {Math.round(totalMinutes)} min
        </h2>
        {plan.rationale && (
          <p className="text-sm leading-relaxed text-muted-foreground">
            <MathText text={plan.rationale} />
          </p>
        )}
      </section>

      <ol className="flex flex-col gap-4">
        {plan.questions.map((question) => (
          <li key={question.number}>
            <PlannedQuestionCard
              assessmentId={assessmentId}
              question={question}
              disabled={generating}
              onSaved={setPlan}
              onWritingChange={(writing) => setWritingCount((count) => count + (writing ? 1 : -1))}
            />
          </li>
        ))}
      </ol>

      {hasExam && (
        <Alert>
          <AlertDescription>
            This assessment already has an exam.{" "}
            <Link href={`${base}/exam`} className="font-medium underline underline-offset-4">
              Open it
            </Link>
            , or generate a new one (it replaces the current exam).
          </AlertDescription>
        </Alert>
      )}
      {actionError && <ErrorAlert message={actionError} />}

      <div className="flex flex-wrap gap-2 border-t pt-6">
        <Button
          size="lg"
          disabled={generating || writingCount > 0}
          onClick={() => (hasExam ? setConfirmingReplace(true) : void generate(false))}
        >
          {generating ? <Spinner /> : <Wand2 />}
          Generate exam
        </Button>
        <Button size="lg" variant="outline" disabled={generating || writingCount > 0} onClick={planAgain}>
          <RefreshCw />
          Make a new plan
        </Button>
      </div>

      <AlertDialog open={confirmingReplace} onOpenChange={setConfirmingReplace}>
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
                setConfirmingReplace(false);
                void generate(true);
              }}
            >
              Replace exam
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function PlannedQuestionCard({
  assessmentId,
  question,
  disabled,
  onSaved,
  onWritingChange,
}: {
  assessmentId: string;
  question: PlannedQuestion;
  /** The exam is being generated. */
  disabled: boolean;
  onSaved: (plan: QuestionPlan) => void;
  onWritingChange: (writing: boolean) => void;
}) {
  const topicId = useId();
  const targetId = useId();
  const ownId = useId();
  const [editing, setEditing] = useState<"plan" | "own" | null>(null);
  const [topic, setTopic] = useState(question.topic);
  const [target, setTarget] = useState(question.assessmentTarget);
  const [own, setOwn] = useState(question.professorQuestion);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [writing, setWriting] = useState(false);
  const [writeError, setWriteError] = useState<string | null>(null);

  async function write() {
    setWriting(true);
    setWriteError(null);
    onWritingChange(true);
    try {
      const saved = await apiRequest<QuestionPlan>(`/api/assessments/${assessmentId}/question-plan/${question.number}/question`, {
        method: "POST",
        freshSession: true,
      });
      onSaved(saved);
    } catch (cause) {
      setWriteError(errorMessage(cause));
    } finally {
      setWriting(false);
      onWritingChange(false);
    }
  }

  function open(mode: "plan" | "own") {
    setTopic(question.topic);
    setTarget(question.assessmentTarget);
    setOwn(question.professorQuestion);
    setError(null);
    setEditing(mode);
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const saved = await apiRequest<QuestionPlan>(`/api/assessments/${assessmentId}/question-plan/${question.number}`, {
        method: "PATCH",
        body: { topic, assessmentTarget: target, professorQuestion: own },
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
    <article aria-label={`Question ${question.number}`} className="flex flex-col gap-3 rounded-xl border bg-card p-4 sm:p-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold">Question {question.number}</h3>
        <Badge variant="secondary">{TYPE_LABELS[question.type]}</Badge>
        <Badge variant="outline">{DIFFICULTY_LABELS[question.difficulty]}</Badge>
        <Badge variant="outline">{BLOOM_LABELS[question.cognitiveLevel]}</Badge>
        <span className="text-xs text-muted-foreground">
          {marks(question.points)} · about {question.estimatedMinutes} min
        </span>
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
                I&apos;m planning one question on{" "}
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
          {question.written && <WrittenQuestionView number={question.number} question={question.written} />}
          {writing && (
            <p role="status" aria-live="polite" className="flex items-center gap-2 text-sm text-muted-foreground">
              <Spinner className="size-3.5" aria-hidden />
              Generating question {question.number}…
            </p>
          )}
          {writeError && <ErrorAlert message={writeError} />}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={writing || disabled} onClick={write}>
              {question.written ? <RefreshCw /> : <Sparkles />}
              {question.written ? "Generate again" : "Generate this question"}
            </Button>
            <Button variant="outline" size="sm" disabled={writing} onClick={() => open("plan")}>
              <Pencil />
              Edit
            </Button>
            <Button variant="outline" size="sm" disabled={writing} onClick={() => open("own")}>
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
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={topicId}>Topic</Label>
                <Input id={topicId} autoFocus maxLength={300} value={topic} onChange={(event) => setTopic(event.target.value)} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={targetId}>What students should do</Label>
                <Textarea id={targetId} maxLength={1000} value={target} onChange={(event) => setTarget(event.target.value)} />
              </div>
            </>
          ) : (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={ownId}>Your own question</Label>
              <Textarea
                id={ownId}
                autoFocus
                className="min-h-28"
                maxLength={4000}
                placeholder="Write the question. ProfPilot keeps your wording and adds what it needs, such as choices and the answer key."
                value={own}
                onChange={(event) => setOwn(event.target.value)}
              />
            </div>
          )}
          {error && <ErrorAlert message={error} />}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={save} disabled={saving || !topic.trim()}>
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
  );
}

/** A question generated on the plan page, with its answer key. */
function WrittenQuestionView({ number, question }: { number: number; question: WrittenQuestion }) {
  return (
    <section
      aria-label={`Generated question ${number}`}
      className="flex flex-col gap-3 rounded-lg border bg-muted/20 p-3 text-sm sm:p-4"
    >
      <QuestionText text={question.prompt} />
      {question.choices && (
        <ol className="flex flex-col gap-1.5" aria-label="Choices">
          {question.choices.map((choice) => (
            <li
              key={choice.id}
              className={cn(
                "flex items-start gap-2 rounded-lg border bg-card px-3 py-2",
                choice.id === question.correctChoice && "border-emerald-600/50 bg-emerald-50 dark:bg-emerald-950/30",
              )}
            >
              <span className="font-medium">{choice.id}.</span>
              <QuestionText text={choice.text} className="min-w-0 flex-1" />
            </li>
          ))}
        </ol>
      )}
      {question.subparts?.map((part) => (
        <div key={part.label} className="flex flex-col gap-1 border-l-2 pl-3">
          <p>
            <span className="font-medium">({part.label})</span>{" "}
            <span className="text-muted-foreground">[{marks(part.points)}]</span>
          </p>
          <QuestionText text={part.prompt} />
          {part.answer && (
            <div className="text-muted-foreground">
              <span className="font-medium text-foreground">Answer:</span>
              <QuestionText text={part.answer} />
            </div>
          )}
        </div>
      ))}
      <div className="flex flex-col gap-2 border-t pt-3">
        {question.answer && !question.subparts && (
          <div>
            <p className="font-medium">Answer</p>
            <QuestionText text={question.answer} />
          </div>
        )}
        {question.solution && (
          <div>
            <p className="font-medium">Solution</p>
            <QuestionText text={question.solution} />
          </div>
        )}
        {question.explanation && (
          <div>
            <p className="font-medium">{question.type === "mcq" ? "Why the other choices are wrong" : "Notes"}</p>
            <QuestionText text={question.explanation} />
          </div>
        )}
        {question.rubric && question.rubric.length > 0 && (
          <div>
            <p className="font-medium">Marking</p>
            <ul className="list-disc pl-5">
              {question.rubric.map((item, index) => (
                <li key={index}>
                  <MathText text={item.criterion} /> ({marks(item.points)})
                </li>
              ))}
            </ul>
          </div>
        )}
        {question.sources.length > 0 && (
          <p className="text-xs text-muted-foreground">Based on: {question.sources.join("; ")}</p>
        )}
      </div>
    </section>
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
      <Skeleton className="h-24 w-full rounded-xl" />
      <Skeleton className="h-32 w-full rounded-xl" />
      <Skeleton className="h-32 w-full rounded-xl" />
    </div>
  );
}
