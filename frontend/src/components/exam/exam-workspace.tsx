"use client";

import { AlertTriangle, MessagesSquare, Play } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useId, useState } from "react";

import { JobProgress } from "@/components/ai/job-progress";
import { DistributionPanel, FinalReviewPanel, QualityCheckPanel } from "@/components/exam/exam-panels";
import { QuestionCard } from "@/components/exam/question-card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError, apiRequest, errorMessage } from "@/lib/api/client";
import { useRun } from "@/lib/api/runs";
import type { AiStatus, Exam, Question, StartedRun } from "@/lib/api/types";

/** The exam editor: every question, the quality check, final review and export. */
export function ExamWorkspace({ assessmentId }: { assessmentId: string }) {
  const [exam, setExam] = useState<Exam | null>(null);
  const [missing, setMissing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [generationRun, setGenerationRun] = useState<string | null>(null);
  const [reviewRun, setReviewRun] = useState<string | null>(null);
  const [versionId, setVersionId] = useState<string | null>(null);
  const [showKey, setShowKey] = useState(true);
  const [actionError, setActionError] = useState<string | null>(null);
  const [resuming, setResuming] = useState(false);
  const showKeyId = useId();
  const base = `/workspace/assessments/${assessmentId}`;

  // Changing this reloads the exam.
  const [reloads, setReloads] = useState(0);
  const load = useCallback(() => setReloads((count) => count + 1), []);

  useEffect(() => {
    let cancelled = false;
    async function fetchExam() {
      try {
        const next = await apiRequest<Exam>(`/api/assessments/${assessmentId}/exam`);
        if (cancelled) return;
        setExam(next);
        setMissing(false);
        setLoadError(null);
        if (next.status === "generating" || next.reviewStatus === "running") {
          const status = await apiRequest<AiStatus>(`/api/assessments/${assessmentId}/ai-status`);
          if (cancelled) return;
          const generating = status.runs.find((run) => run.kind === "generation" && run.status === "running");
          const reviewing = status.runs.find(
            (run) => (run.kind === "review" || run.kind === "versioning") && run.status === "running",
          );
          if (generating) setGenerationRun(generating.id);
          if (reviewing) setReviewRun(reviewing.id);
        }
      } catch (cause) {
        if (cancelled) return;
        if (cause instanceof ApiError && cause.status === 404) setMissing(true);
        else setLoadError(errorMessage(cause));
      }
    }
    void fetchExam();
    return () => {
      cancelled = true;
    };
  }, [assessmentId, reloads]);

  const generation = useRun(generationRun, () => {
    setGenerationRun(null);
    load();
  });
  // Followed only for when it finishes.
  useRun(reviewRun, (finished) => {
    setReviewRun(null);
    if (finished.status === "failed") setActionError(finished.errorMessage);
    load();
  });

  async function runReview() {
    if (!exam) return;
    setActionError(null);
    try {
      const started = await apiRequest<StartedRun>(`/api/exams/${exam.id}/review`, { method: "POST", freshSession: true });
      setReviewRun(started.run.id);
    } catch (cause) {
      setActionError(errorMessage(cause));
    }
  }

  async function resume() {
    setResuming(true);
    setActionError(null);
    try {
      const started = await apiRequest<{ run: { id: string } }>(`/api/assessments/${assessmentId}/exam`, {
        method: "POST",
        body: { mode: "full", resume: true },
        freshSession: true,
      });
      setGenerationRun(started.run.id);
      setExam((current) => (current ? { ...current, status: "generating" } : current));
    } catch (cause) {
      setActionError(errorMessage(cause));
    } finally {
      setResuming(false);
    }
  }

  if (loadError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>The exam couldn&apos;t be loaded</AlertTitle>
        <AlertDescription>
          {loadError}{" "}
          <button type="button" className="underline underline-offset-4" onClick={() => void load()}>
            Try again
          </button>
        </AlertDescription>
      </Alert>
    );
  }
  if (missing) {
    return (
      <Empty className="border border-dashed py-14">
        <EmptyHeader>
          <EmptyTitle>No exam yet</EmptyTitle>
          <EmptyDescription>Review and approve the assessment plan, then generate the exam or build it with AI.</EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Link href={`${base}/plan`} className={buttonVariants()}>
            Open the plan
          </Link>
        </EmptyContent>
      </Empty>
    );
  }
  if (!exam) return <ExamSkeleton />;

  if (exam.status === "generating") {
    return (
      <div className="flex flex-col gap-4">
        <JobProgress run={generation.run} startingLabel="Generating the exam…" />
        {exam.questions.length > 0 && (
          <p className="text-sm text-muted-foreground">
            {exam.questions.length} question{exam.questions.length === 1 ? "" : "s"} written so far.
          </p>
        )}
      </div>
    );
  }

  const version = exam.versions.find((item) => item.id === versionId) ?? exam.versions[0];
  const questions = exam.questions
    .filter((question) => question.versionId === version?.id)
    .sort((a, b) => a.number - b.number);
  const sections = groupBySection(questions, exam);

  // Swaps a question with its neighbour in the version on screen.
  async function swap(question: Question, neighbour: Question) {
    if (!exam || !version) return;
    const ids = questions.map((item) => item.id);
    const from = ids.indexOf(question.id);
    const to = ids.indexOf(neighbour.id);
    [ids[from], ids[to]] = [ids[to], ids[from]];
    await apiRequest<Exam>(`/api/exams/${exam.id}/versions/${version.id}/order`, {
      method: "PUT",
      body: { questionIds: ids },
    });
  }

  return (
    <div className="flex flex-col gap-6">
      {exam.status === "failed" && (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>Generation stopped before it finished</AlertTitle>
          <AlertDescription>
            <p>{exam.errorMessage}</p>
            {exam.mode === "full" && (
              <Button type="button" size="sm" className="mt-2" onClick={resume} disabled={resuming}>
                {resuming ? <Spinner /> : <Play />}
                Continue generation
              </Button>
            )}
          </AlertDescription>
        </Alert>
      )}
      {exam.status === "building" && (
        <Alert>
          <MessagesSquare />
          <AlertTitle>You&apos;re building this exam with AI</AlertTitle>
          <AlertDescription>
            You can edit questions here too.{" "}
            <Link href={`${base}/builder`} className="font-medium underline underline-offset-4">
              Continue building
            </Link>{" "}
            to add questions, create the other versions and run the quality check.
          </AlertDescription>
        </Alert>
      )}
      {actionError && (
        <Alert variant="destructive">
          <AlertDescription>{actionError}</AlertDescription>
        </Alert>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        {exam.versions.length > 1 ? (
          <Tabs value={version.id} onValueChange={(value) => setVersionId(String(value))}>
            <TabsList aria-label="Exam versions">
              {exam.versions.map((item) => (
                <TabsTrigger key={item.id} value={item.id}>
                  Version {item.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        ) : (
          <span />
        )}
        <div className="flex items-center gap-2">
          <Checkbox id={showKeyId} checked={showKey} onCheckedChange={(checked) => setShowKey(Boolean(checked))} />
          <Label htmlFor={showKeyId} className="font-normal">
            Show answer keys
          </Label>
        </div>
      </div>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="flex min-w-0 flex-col gap-8">
          {questions.length === 0 && (
            <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">This version has no questions yet.</p>
          )}
          {sections.map((section) => (
            <section key={section.key} aria-label={section.title ?? undefined} className="flex flex-col gap-4">
              {section.title && (
                <div className="flex flex-col gap-1 border-b pb-2">
                  <h2 className="text-base font-semibold tracking-tight">{section.title}</h2>
                  {section.instructions && <p className="text-sm text-muted-foreground">{section.instructions}</p>}
                </div>
              )}
              {section.questions.map((question, index, group) => (
                <QuestionCard
                  key={question.id}
                  question={question}
                  showKey={showKey}
                  multiVersion={exam.versions.length > 1}
                  onChanged={() => void load()}
                  move={{
                    up: index > 0 ? () => swap(question, group[index - 1]) : null,
                    down: index < group.length - 1 ? () => swap(question, group[index + 1]) : null,
                  }}
                />
              ))}
            </section>
          ))}
        </div>

        <aside className="flex flex-col gap-4 xl:sticky xl:top-10 xl:max-h-[calc(100svh-5rem)] xl:overflow-y-auto">
          {version && <DistributionPanel exam={exam} version={version} />}
          <QualityCheckPanel
            exam={exam}
            versionLabel={version?.label ?? "A"}
            running={Boolean(reviewRun) || exam.reviewStatus === "running"}
            onRun={runReview}
          />
          <FinalReviewPanel exam={exam} onFinalized={() => void load()} />
        </aside>
      </div>
    </div>
  );
}

function groupBySection(questions: Question[], exam: Exam) {
  const titles = new Map(exam.sections.map((section) => [section.id, section]));
  const groups: {
    key: string;
    sectionId: string | null;
    title: string | null;
    instructions: string | null;
    questions: Question[];
  }[] = [];
  for (const question of questions) {
    const last = groups.at(-1);
    if (last && last.sectionId === question.sectionId) {
      last.questions.push(question);
      continue;
    }
    const section = question.sectionId ? titles.get(question.sectionId) : undefined;
    groups.push({
      // Unique even if a section appears twice (after reordering).
      key: `${groups.length}-${question.sectionId ?? "none"}`,
      sectionId: question.sectionId,
      title: section && exam.sections.length > 1 ? section.title : null,
      instructions: section?.instructions ?? null,
      questions: [question],
    });
  }
  return groups;
}

function ExamSkeleton() {
  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_20rem]" aria-busy="true">
      <p role="status" className="sr-only">
        Loading the exam…
      </p>
      <div className="flex flex-col gap-4">
        <Skeleton className="h-40 w-full rounded-xl" />
        <Skeleton className="h-40 w-full rounded-xl" />
      </div>
      <Skeleton className="h-64 w-full rounded-xl" />
    </div>
  );
}
