"use client";

import { AlertTriangle, KeyRound, MessagesSquare, Play } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useId, useState } from "react";

import { JobProgress } from "@/components/ai/job-progress";
import { DistributionPanel, FinalReviewPanel, QualityCheckPanel } from "@/components/exam/exam-panels";
import { hasAnswerKey } from "@/components/exam/labels";
import { QuestionCard } from "@/components/exam/question-card";
import { QuestionText } from "@/components/exam/question-text";
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

/** While the exam is being written, its questions appear here as soon as each is saved. */
const REFRESH_WHILE_GENERATING_MS = 2000;

/** The exam editor: every question, the quality check, final review and export. */
export function ExamWorkspace({ assessmentId }: { assessmentId: string }) {
  const [exam, setExam] = useState<Exam | null>(null);
  const [missing, setMissing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [generationRun, setGenerationRun] = useState<string | null>(null);
  const [reviewRun, setReviewRun] = useState<{ id: string; kind: string } | null>(null);
  // Generate all answer keys, while it runs (keys appear under their questions as they are written).
  const [keysRun, setKeysRun] = useState<string | null>(null);
  const [keysDone, setKeysDone] = useState(false);
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
        // Pick up jobs already running: the questions being written, the answer keys, the other versions or the check.
        const status = await apiRequest<AiStatus>(`/api/assessments/${assessmentId}/ai-status`);
        if (cancelled) return;
        const running = status.runs.filter((run) => run.status === "running");
        const writingKeys = running.find((run) => run.kind === "generation" && run.stage === "writing_answer_keys");
        const generating = running.find((run) => run.kind === "generation" && run !== writingKeys);
        const reviewing = running.find((run) => run.kind === "review" || run.kind === "versioning");
        if (generating && next.status === "generating") setGenerationRun(generating.id);
        if (writingKeys) setKeysRun(writingKeys.id);
        if (reviewing) setReviewRun({ id: reviewing.id, kind: reviewing.kind });
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

  // While the exam (or its answer keys) is being written, each question (or key) appears as soon as it is saved.
  const generating = exam?.status === "generating" || keysRun !== null;
  useEffect(() => {
    if (!generating) return;
    const timer = setInterval(() => {
      apiRequest<Exam>(`/api/assessments/${assessmentId}/exam`)
        .then(setExam)
        .catch(() => undefined); // the next refresh, or the end of the job, reloads it
    }, REFRESH_WHILE_GENERATING_MS);
    return () => clearInterval(timer);
  }, [generating, assessmentId]);

  const generation = useRun(generationRun, () => {
    setGenerationRun(null);
    load();
  });
  useRun(keysRun, (finished) => {
    setKeysRun(null);
    if (finished.status === "failed") setActionError(finished.errorMessage);
    else setKeysDone(true);
    load();
  });
  // Followed only for when it finishes.
  useRun(reviewRun?.id ?? null, (finished) => {
    setReviewRun(null);
    if (finished.status === "failed") setActionError(finished.errorMessage);
    load();
  });

  async function runReview() {
    if (!exam) return;
    setActionError(null);
    try {
      const started = await apiRequest<StartedRun>(`/api/exams/${exam.id}/review`, { method: "POST", freshSession: true });
      setReviewRun({ id: started.run.id, kind: "review" });
    } catch (cause) {
      setActionError(errorMessage(cause));
    }
  }

  /** Generate all answer keys: every missing or out-of-date key, from the questions as saved. */
  async function startKeys() {
    if (!exam) return;
    setActionError(null);
    setKeysDone(false);
    const started = await apiRequest<StartedRun>(`/api/exams/${exam.id}/answer-keys`, { method: "POST", freshSession: true });
    setKeysRun(started.run.id);
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
          <EmptyDescription>Read your improved prompt, then go to the question plan to generate the exam.</EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Link href={`${base}/plan`} className={buttonVariants()}>
            Open your prompt
          </Link>
        </EmptyContent>
      </Empty>
    );
  }
  if (!exam) return <ExamSkeleton />;

  if (exam.status === "generating") {
    // Question 1, 2, 3… in order: each one as soon as it is written (at its planned number), the others
    // shown as being written. Editing opens once the whole exam is written.
    const first = exam.versions[0];
    const written = new Map(
      exam.questions.filter((question) => question.versionId === first?.id).map((question) => [question.position, question]),
    );
    const planned = Math.max(exam.plannedQuestionCount, 0, ...written.keys());
    return (
      <div className="flex flex-col gap-6">
        {planned === 0 ? (
          <JobProgress run={generation.run} startingLabel="Generating the exam…" />
        ) : (
          <Alert role="status">
            <Spinner />
            <AlertTitle>
              Generating the exam: {written.size} of {planned} {planned === 1 ? "question" : "questions"} ready
            </AlertTitle>
            <AlertDescription>
              Each question appears below, in plan order, as soon as it is ready. Start reading
              while the others are being generated; you can edit them once the whole exam is generated.
            </AlertDescription>
          </Alert>
        )}
        {planned > 0 && (
          <>
            <div className="flex items-center justify-end gap-2">
              <Checkbox id={showKeyId} checked={showKey} onCheckedChange={(checked) => setShowKey(Boolean(checked))} />
              <Label htmlFor={showKeyId} className="font-normal">
                Show answer keys
              </Label>
            </div>
            {plannedGroups(exam, planned, written).map((group) => (
              // Each case is shown with its questions, before the first one is even written.
              <section key={group.key} aria-label={group.title ?? "Questions"} className="flex flex-col gap-4">
                {(group.title || group.instructions) && (
                  <div className="flex flex-col gap-2 border-b pb-3">
                    {group.title && <h2 className="text-base font-semibold tracking-tight">{group.title}</h2>}
                    {group.instructions && <QuestionText text={group.instructions} />}
                  </div>
                )}
                <ol className="flex flex-col gap-6" aria-label={group.title ? `${group.title}: questions` : "Questions"}>
                  {group.numbers.map((number) => {
                    const question = written.get(number);
                    return (
                      <li key={number}>
                        {question ? (
                          <QuestionCard
                            question={{ ...question, number }}
                            headingLevel={group.title ? 3 : 2}
                            showKey={showKey}
                            multiVersion={false}
                            readOnly
                            onChanged={() => undefined}
                          />
                        ) : (
                          <div
                            role="status"
                            className="flex items-center gap-2 rounded-xl border border-dashed p-4 text-sm text-muted-foreground sm:p-5"
                          >
                            <Spinner />
                            Generating question {number}…
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ol>
              </section>
            ))}
          </>
        )}
      </div>
    );
  }

  const reviewing = Boolean(reviewRun) || exam.reviewStatus === "running";
  const keyCounts = {
    total: exam.questions.length,
    ready: exam.questions.filter(
      (question) => hasAnswerKey(question) && !(question.answerKeyOutdated ?? question.needsSolutionReview),
    ).length,
  };
  const creatingVersions = reviewRun?.kind === "versioning";
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
      {exam.status === "building" && exam.mode === "full" && (
        <Alert>
          <MessagesSquare />
          <AlertTitle>This exam is still being built from its question plan</AlertTitle>
          <AlertDescription>
            You can edit questions here too.{" "}
            <Link href={`${base}/question-plan`} className="font-medium underline underline-offset-4">
              Continue on the plan
            </Link>{" "}
            to generate the remaining questions and create the other versions.
          </AlertDescription>
        </Alert>
      )}
      {exam.status === "building" && exam.mode === "interactive" && (
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
      {keysRun && (
        <Alert role="status">
          <Spinner />
          <AlertTitle>
            Writing answer keys: {keyCounts.ready} of {keyCounts.total} ready
          </AlertTitle>
          <AlertDescription>
            Each key appears under its question as soon as it is written, from the question as you saved it. You can keep
            reading.
          </AlertDescription>
        </Alert>
      )}
      {keysDone && !keysRun && (
        <Alert>
          <KeyRound />
          <AlertTitle>
            {keyCounts.ready === keyCounts.total
              ? "All answer keys are ready"
              : `Answer keys written: ${keyCounts.ready} of ${keyCounts.total} ready`}
          </AlertTitle>
          <AlertDescription>
            {keyCounts.ready === keyCounts.total
              ? "Download them from Final review & export: the answer key alone, or with the questions."
              : "A question changed while its key was being written, so its key wasn't kept. Generate all answer keys again to write the rest."}
          </AlertDescription>
        </Alert>
      )}
      {reviewing && (
        <Alert role="status">
          <Spinner />
          <AlertTitle>{creatingVersions ? "Creating the other versions" : "The quality check is running"}</AlertTitle>
          <AlertDescription>
            {creatingVersions
              ? "You can keep reading and editing. The other versions appear when they are ready, without answer keys, like the first."
              : "You can keep working. A question you approve now is locked, so the check will report its issues but can't fix them; approve after the check to let it apply safe fixes."}
          </AlertDescription>
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
                  {/* What students read before the section's questions: what to do, or a case with its data. */}
                  {section.instructions && <QuestionText text={section.instructions} />}
                </div>
              )}
              {section.questions.map((question, index, group) => (
                <QuestionCard
                  key={question.id}
                  question={question}
                  headingLevel={section.title ? 3 : 2}
                  showKey={showKey}
                  multiVersion={exam.versions.length > 1}
                  reviewing={reviewing}
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

        <aside aria-label="Exam review and export" className="flex flex-col gap-4 xl:sticky xl:top-[88px] xl:max-h-[calc(100svh-7rem)] xl:overflow-y-auto">
          {version && <DistributionPanel exam={exam} version={version} />}
          <QualityCheckPanel
            exam={exam}
            versionLabel={version?.label ?? "A"}
            running={reviewing}
            onRun={runReview}
          />
          <FinalReviewPanel exam={exam} onFinalized={() => void load()} keys={{ running: keysRun !== null, start: startKeys }} />
        </aside>
      </div>
    </div>
  );
}

/** While the exam is written: planned numbers 1…planned under their sections, each case with its text. */
function plannedGroups(exam: Exam, planned: number, written: Map<number, Question>) {
  const sections = [...exam.sections].sort((a, b) => a.position - b.position);
  const byId = new Map(sections.map((section, index) => [section.id, index]));
  const groups: { key: string; title: string | null; instructions: string | null; numbers: number[] }[] = [];
  let currentIndex: number | null = null;
  for (let number = 1; number <= planned; number++) {
    const sectionId = written.get(number)?.sectionId;
    const index = exam.plannedSections?.[number - 1] ?? (sectionId ? byId.get(sectionId) : undefined) ?? 0;
    const last = groups.at(-1);
    if (last && index === currentIndex) {
      last.numbers.push(number);
      continue;
    }
    currentIndex = index;
    const section = sections[index];
    groups.push({
      key: `${groups.length}-${index}`,
      title: section && sections.length > 1 ? section.title : null,
      instructions: section?.instructions ?? null,
      numbers: [number],
    });
  }
  return groups;
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
