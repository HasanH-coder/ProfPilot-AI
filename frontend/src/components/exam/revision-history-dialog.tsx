"use client";

import { RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";

import { REVISION_SOURCES } from "@/components/exam/labels";
import { QuestionText } from "@/components/exam/question-text";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { apiRequest, errorMessage } from "@/lib/api/client";
import type { Question, Revision } from "@/lib/api/types";
import { timeAgo } from "@/lib/time";

type RevisionHistoryDialogProps = {
  question: Question;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRestored: () => void;
};

/** Earlier versions of a question, newest first, each restorable. */
export function RevisionHistoryDialog({ question, open, onOpenChange, onRestored }: RevisionHistoryDialogProps) {
  // Results are tagged with the question version they belong to, so a changed
  // question shows "loading" instead of an out-of-date history.
  const version = `${question.id}@${question.updatedAt}`;
  const [loaded, setLoaded] = useState<{ version: string; revisions?: Revision[]; error?: string } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [restoring, setRestoring] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    apiRequest<Revision[]>(`/api/questions/${question.id}/revisions`)
      .then((rows) => !cancelled && setLoaded({ version, revisions: rows }))
      .catch((cause) => !cancelled && setLoaded({ version, error: errorMessage(cause) }));
    return () => {
      cancelled = true;
    };
  }, [open, question.id, version]);

  const current = loaded?.version === version ? loaded : null;
  const revisions = current?.revisions ?? null;
  const error = actionError ?? current?.error ?? null;

  async function restore(revision: Revision) {
    setRestoring(revision.id);
    setActionError(null);
    try {
      await apiRequest(`/api/questions/${question.id}/revisions/${revision.id}/restore`, { method: "POST" });
      onRestored();
      onOpenChange(false);
    } catch (cause) {
      setActionError(errorMessage(cause));
    } finally {
      setRestoring(null);
    }
  }

  const locked = question.status === "approved";
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="workspace-theme max-h-[85svh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>History of question {question.number}</DialogTitle>
          <DialogDescription>
            Each entry is the question as it was before a change. Restoring it keeps the current version in the history
            too.
          </DialogDescription>
        </DialogHeader>
        {locked && (
          <Alert>
            <AlertDescription>Unlock the question to restore an earlier version.</AlertDescription>
          </Alert>
        )}
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {revisions === null && !error ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : revisions && revisions.length === 0 ? (
          <p className="text-sm text-muted-foreground">This question hasn&apos;t been changed yet.</p>
        ) : (
          <ol className="flex flex-col gap-3">
            {revisions?.map((revision) => (
              <li key={revision.id} className="flex flex-col gap-2 rounded-lg border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm font-medium">
                    {REVISION_SOURCES[revision.source]}{" "}
                    <span className="font-normal text-muted-foreground">· {timeAgo(revision.createdAt)}</span>
                  </p>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={locked || restoring !== null}
                    onClick={() => void restore(revision)}
                  >
                    {restoring === revision.id ? <Spinner /> : <RotateCcw />}
                    Restore
                  </Button>
                </div>
                {revision.instruction && <p className="text-sm text-muted-foreground">“{revision.instruction}”</p>}
                <details className="text-sm">
                  <summary className="cursor-pointer text-muted-foreground">Show the question before this change</summary>
                  <QuestionText className="mt-2 rounded-lg bg-muted/40 p-2" text={revision.snapshot.prompt as string | undefined} />
                </details>
              </li>
            ))}
          </ol>
        )}
      </DialogContent>
    </Dialog>
  );
}
