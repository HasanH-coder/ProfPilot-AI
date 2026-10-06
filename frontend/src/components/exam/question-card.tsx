"use client";

import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Check,
  CircleCheck,
  CopyCheck,
  History,
  KeyRound,
  Lock,
  MoreHorizontal,
  Pencil,
  Sparkles,
  Trash2,
  Unlock,
} from "lucide-react";
import { memo, useId, useState, type FormEvent } from "react";

import { FigurePreview } from "@/components/exam/figure-preview";
import { DIFFICULTY_LABELS, marks, QUICK_ACTIONS, TYPE_LABELS, type QuickAction } from "@/components/exam/labels";
import { QuestionEditDialog } from "@/components/exam/question-edit-dialog";
import { QuestionText } from "@/components/exam/question-text";
import { RevisionHistoryDialog } from "@/components/exam/revision-history-dialog";
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
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { apiRequest, errorMessage } from "@/lib/api/client";
import type { DistributionWarning, Question, QuestionResult } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type QuestionCardProps = {
  question: Question;
  headingLevel?: 2 | 3;
  /** Show the answer key (answers, solutions, rubrics) inline. */
  showKey: boolean;
  multiVersion: boolean;
  /** Called after any change, so the exam can be reloaded. */
  onChanged: () => void;
  highlighted?: boolean;
  /** Moving within the section; a direction is null where the question can't go. Omit to hide moving. */
  move?: { up: (() => Promise<void>) | null; down: (() => Promise<void>) | null };
};

type Notice = { summary: string | null; warnings: DistributionWarning[] };

/**
 * One question, with everything the professor can do to it. Memoized: with the
 * same props (an unchanged question) it doesn't re-render while the rest of the
 * page updates, e.g. during a voice call.
 */
export const QuestionCard = memo(function QuestionCard({
  question,
  headingLevel = 2,
  showKey,
  multiVersion,
  onChanged,
  highlighted,
  move,
}: QuestionCardProps) {
  const Heading = headingLevel === 3 ? "h3" : "h2";
  const headingId = useId();
  const askId = useId();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [asking, setAsking] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [editOpen, setEditOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const locked = question.status === "approved";
  const base = `/api/questions/${question.id}`;

  async function run(label: string, action: () => Promise<QuestionResult | void>) {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      const result = await action();
      if (result && (result.changeSummary || result.distributionWarnings?.length)) {
        setNotice({ summary: result.changeSummary ?? null, warnings: result.distributionWarnings ?? [] });
      }
      onChanged();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(null);
    }
  }

  const revise = (preset: QuickAction | null, text?: string) =>
    run(preset ? `${QUICK_ACTIONS.find((item) => item.preset === preset)?.label}…` : "Revising…", () =>
      apiRequest<QuestionResult>(`${base}/revision`, { method: "POST", body: { preset, instruction: text || null } }),
    );

  async function handleAsk(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const text = instruction.trim();
    if (!text) return;
    await revise(null, text);
    setInstruction("");
    setAsking(false);
  }

  return (
    <article
      id={`question-${question.id}`}
      aria-labelledby={headingId}
      aria-busy={busy !== null}
      className={cn(
        "relative flex scroll-mt-24 flex-col gap-4 rounded-xl border bg-card p-4 transition-shadow duration-700 sm:p-5",
        highlighted && "ring-2 ring-primary/35",
      )}
    >
      <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Heading id={headingId} className="font-semibold">
            Question {question.number}
          </Heading>
          <Badge variant="secondary">{TYPE_LABELS[question.type]}</Badge>
          <Badge variant="outline">{DIFFICULTY_LABELS[question.difficulty]}</Badge>
          <span className="text-sm text-muted-foreground">{marks(question.points)}</span>
          {question.estimatedMinutes ? (
            <span className="text-sm text-muted-foreground">· ~{question.estimatedMinutes} min</span>
          ) : null}
          {locked && (
            <Badge variant="outline" className="border-emerald-600/40 text-emerald-700 dark:text-emerald-400">
              <Lock />
              Approved
            </Badge>
          )}
          {question.needsSolutionReview && (
            <Badge variant="outline" className="border-amber-500/50 text-amber-700 dark:text-amber-400">
              <AlertTriangle />
              Check answer key
            </Badge>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy !== null || locked}
            aria-expanded={asking}
            aria-controls={askId}
            onClick={() => setAsking((open) => !open)}
          >
            <Sparkles />
            Ask AI
          </Button>
          <Button
            type="button"
            variant={locked ? "secondary" : "outline"}
            size="sm"
            disabled={busy !== null}
            onClick={() =>
              run(locked ? "Unlocking…" : "Approving…", () =>
                apiRequest<QuestionResult>(`${base}/approval`, { method: "PUT", body: { approved: !locked } }),
              )
            }
          >
            {locked ? <Unlock /> : <Check />}
            {locked ? "Unlock" : "Approve"}
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`More actions for question ${question.number}`}
                  disabled={busy !== null}
                />
              }
            >
              <MoreHorizontal />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="workspace-theme w-60">
              <DropdownMenuGroup>
                <DropdownMenuLabel>Change with AI</DropdownMenuLabel>
                {QUICK_ACTIONS.map((action) => (
                  <DropdownMenuItem key={action.preset} disabled={locked} onClick={() => void revise(action.preset)}>
                    <Sparkles />
                    {action.label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuGroup>
              <DropdownMenuSeparator />
              <DropdownMenuItem disabled={locked} onClick={() => setEditOpen(true)}>
                <Pencil />
                Edit manually
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={locked}
                onClick={() =>
                  void run("Regenerating the answer key…", () =>
                    apiRequest<QuestionResult>(`${base}/solution`, { method: "POST" }),
                  )
                }
              >
                <KeyRound />
                Regenerate answer key
              </DropdownMenuItem>
              {multiVersion && (
                <DropdownMenuItem
                  onClick={() =>
                    void run("Updating the other versions…", async () => {
                      await apiRequest(`${base}/variants`, { method: "POST" });
                    })
                  }
                >
                  <CopyCheck />
                  Update other versions to match
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onClick={() => setHistoryOpen(true)}>
                <History />
                Revision history{question.revisionCount > 0 ? ` (${question.revisionCount})` : ""}
              </DropdownMenuItem>
              {move && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem disabled={!move.up} onClick={() => move.up && void run("Moving…", move.up)}>
                    <ArrowUp />
                    Move up
                  </DropdownMenuItem>
                  <DropdownMenuItem disabled={!move.down} onClick={() => move.down && void run("Moving…", move.down)}>
                    <ArrowDown />
                    Move down
                  </DropdownMenuItem>
                </>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" disabled={locked} onClick={() => setDeleteOpen(true)}>
                <Trash2 />
                Delete question
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>

      <QuestionText text={question.prompt} />
      {question.figureDocumentId && <FigurePreview documentId={question.figureDocumentId} />}

      {question.choices && (
        <ol className="flex flex-col gap-1.5" aria-label="Choices">
          {question.choices.map((choice) => {
            const correct = showKey && choice.id === question.correctChoice;
            return (
              <li
                key={choice.id}
                className={cn(
                  "flex items-start gap-2 rounded-lg border px-3 py-2 text-sm",
                  correct && "border-emerald-600/50 bg-emerald-50 dark:bg-emerald-950/30",
                )}
              >
                <span className="font-medium">{choice.id}.</span>
                <span className="min-w-0 flex-1 break-words whitespace-pre-wrap">{choice.text}</span>
                {correct && (
                  <span className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-emerald-700 dark:text-emerald-400">
                    <CircleCheck className="size-3.5" />
                    Correct
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      )}

      {question.subparts && (
        <ol className="flex flex-col gap-3">
          {question.subparts.map((part) => (
            <li key={part.label} className="flex flex-col gap-1 border-l-2 pl-3">
              <p className="text-sm">
                <span className="font-medium">({part.label})</span> {part.prompt}{" "}
                <span className="text-muted-foreground">[{marks(part.points)}]</span>
              </p>
              {showKey && part.answer && (
                <p className="text-sm text-muted-foreground">
                  <span className="font-medium text-foreground">Answer:</span> {part.answer}
                </p>
              )}
            </li>
          ))}
        </ol>
      )}

      {showKey && <AnswerKey question={question} />}

      {asking && !locked && (
        <form id={askId} onSubmit={handleAsk} className="flex flex-col gap-2 rounded-lg border bg-muted/30 p-3">
          <label htmlFor={`${askId}-input`} className="text-sm font-medium">
            How should ProfPilot change this question?
          </label>
          <Textarea
            id={`${askId}-input`}
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
            placeholder="e.g. Make it harder but keep the same concept · Use a scenario instead · Change part (b) only"
            maxLength={2000}
            className="min-h-20"
          />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" size="sm" disabled={!instruction.trim() || busy !== null}>
              <Sparkles />
              Change question
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setAsking(false)}>
              Cancel
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Only this question changes. Its answer key and rubric are rewritten to match.
          </p>
        </form>
      )}

      {busy && (
        <p role="status" aria-live="polite" className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner className="size-3.5" aria-hidden />
          {busy}
        </p>
      )}
      {notice && (
        <div role="status" className="flex flex-col gap-1 rounded-lg bg-muted/50 px-3 py-2 text-sm">
          {notice.summary && <p>{notice.summary}</p>}
          {notice.warnings.map((warning) => (
            <p key={`${warning.dimension}-${warning.label}`} className="flex items-start gap-1.5 text-amber-700 dark:text-amber-400">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              {warning.message}
            </p>
          ))}
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      <QuestionEditDialog
        // A changed question starts the dialog fresh.
        key={question.updatedAt}
        question={question}
        open={editOpen}
        onOpenChange={setEditOpen}
        onSaved={(result) => {
          setNotice(result.distributionWarnings.length ? { summary: null, warnings: result.distributionWarnings } : null);
          onChanged();
        }}
      />
      <RevisionHistoryDialog
        question={question}
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        onRestored={onChanged}
      />
      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent className="workspace-theme">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete question {question.number}?</AlertDialogTitle>
            <AlertDialogDescription>
              {multiVersion
                ? "It is deleted from every version of the exam, so the versions stay equivalent."
                : "This can't be undone."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => void run("Deleting…", () => apiRequest(base, { method: "DELETE" }))}
            >
              Delete question
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </article>
  );
});

function AnswerKey({ question }: { question: Question }) {
  const hasRubric = question.rubric && question.rubric.length > 0;
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-dashed p-3 text-sm">
      <p className="flex items-center gap-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">
        <KeyRound className="size-3.5" />
        Answer key
      </p>
      {question.type === "mcq" && (
        <p>
          <span className="font-medium">Correct answer:</span> {question.correctChoice}
        </p>
      )}
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
      {hasRubric && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-64 text-left text-sm">
            <caption className="sr-only">Marking rubric</caption>
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th scope="col" className="py-1 pr-3 font-medium">Criterion</th>
                <th scope="col" className="py-1 text-right font-medium">Marks</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {question.rubric?.map((item, index) => (
                <tr key={index}>
                  <td className="py-1.5 pr-3">{item.criterion}</td>
                  <td className="py-1.5 text-right tabular-nums">{item.points}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {question.sources.length > 0 && (
        <p className="text-xs text-muted-foreground">Based on: {question.sources.join("; ")}</p>
      )}
    </div>
  );
}
