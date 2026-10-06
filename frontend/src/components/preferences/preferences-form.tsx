"use client";

import { Check, Eraser } from "lucide-react";
import { useEffect, useId, useState, type FormEvent } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { apiRequest, errorMessage } from "@/lib/api/client";
import type { Preferences } from "@/lib/api/types";
import { timeAgo } from "@/lib/time";

/** The professor's assessment preferences: what they wrote, and what ProfPilot learned. */
export function PreferencesForm() {
  const notesId = useId();
  const learningId = useId();
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [notes, setNotes] = useState("");
  const [learning, setLearning] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiRequest<Preferences>("/api/preferences")
      .then((result) => {
        setPreferences(result);
        setNotes(result.explicitNotes);
        setLearning(result.learningEnabled);
      })
      .catch((cause) => setError(errorMessage(cause)));
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const result = await apiRequest<Preferences>("/api/preferences", {
        method: "PUT",
        body: { explicitNotes: notes, learningEnabled: learning },
      });
      setPreferences(result);
      setSaved(true);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setSaving(false);
    }
  }

  async function reset() {
    setError(null);
    try {
      setPreferences(await apiRequest<Preferences>("/api/preferences/learned", { method: "DELETE" }));
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }

  if (!preferences) {
    return error ? (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    ) : (
      <div className="flex flex-col gap-4" aria-busy="true">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-32 w-full rounded-xl" />
      </div>
    );
  }

  const learned = preferences.learned;
  const lines = learned.summary ?? [];

  return (
    <div className="flex flex-col gap-10">
      <form onSubmit={save} className="flex flex-col gap-6">
        <section className="flex flex-col gap-3">
          <div className="flex flex-col gap-1 border-b pb-3">
            <h2 className="text-base font-semibold tracking-tight">
              <label htmlFor={notesId}>Your preferences</label>
            </h2>
            <p className="text-sm text-muted-foreground">
              How you like your assessments, in your own words. ProfPilot applies them gently; the settings of each
              assessment always come first.
            </p>
          </div>
          <Textarea
            id={notesId}
            className="min-h-32"
            maxLength={2000}
            value={notes}
            onChange={(event) => {
              setNotes(event.target.value);
              setSaved(false);
            }}
            placeholder={"e.g. Prefer scenario-based questions with real-world data.\nAvoid trick questions.\nUse subparts for long problems."}
          />
        </section>
        <div className="flex items-start gap-2">
          <Checkbox
            id={learningId}
            checked={learning}
            onCheckedChange={(checked) => {
              setLearning(Boolean(checked));
              setSaved(false);
            }}
          />
          <div className="flex flex-col gap-1">
            <Label htmlFor={learningId}>Learn from my finalized exams and approved revisions</Label>
            <p className="text-sm text-muted-foreground">
              Only work you accept counts: exams you mark as final, and AI changes you approve. Drafts and undone
              changes never do.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={saving}>
            {saving ? <Spinner /> : <Check />}
            Save preferences
          </Button>
          {saved && (
            <p role="status" className="text-sm text-muted-foreground">
              Saved
            </p>
          )}
        </div>
      </form>

      <section className="flex flex-col gap-3">
        <div className="flex flex-col gap-1 border-b pb-3">
          <h2 className="text-base font-semibold tracking-tight">What ProfPilot has learned</h2>
          <p className="text-sm text-muted-foreground">
            {learned.based_on
              ? `From ${learned.based_on} finalized exam${learned.based_on === 1 ? "" : "s"} and ${learned.accepted_revisions ?? 0} approved AI revision${learned.accepted_revisions === 1 ? "" : "s"}.`
              : "Nothing yet. Mark an exam as final in its final review to start."}
          </p>
        </div>
        {lines.length > 0 ? (
          <ul className="flex list-disc flex-col gap-1.5 pl-5 text-sm">
            {lines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        ) : null}
        {(lines.length > 0 || learned.based_on) && (
          <AlertDialog>
            <AlertDialogTrigger render={<Button variant="outline" className="w-fit" />}>
              <Eraser />
              Forget what ProfPilot learned
            </AlertDialogTrigger>
            <AlertDialogContent className="workspace-theme">
              <AlertDialogHeader>
                <AlertDialogTitle>Forget what ProfPilot learned?</AlertDialogTitle>
                <AlertDialogDescription>
                  The learned tendencies are deleted. Your written preferences and your exams stay as they are.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction variant="destructive" onClick={() => void reset()}>
                  Forget
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        )}
        {preferences.updatedAt && <p className="text-xs text-muted-foreground">Last updated {timeAgo(preferences.updatedAt)}</p>}
      </section>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
    </div>
  );
}
