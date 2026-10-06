"use client";

import { Trash2 } from "lucide-react";
import { unstable_rethrow } from "next/navigation";
import { useState, useTransition } from "react";

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
import { Spinner } from "@/components/ui/spinner";
import { deleteAssessment } from "@/lib/assessments/actions";

type DeleteAssessmentDialogProps = {
  assessmentId: string;
  /** Blank when the assessment has no name. */
  examName: string;
  fileCount: number;
};

/** A Delete button that asks for confirmation before deleting the assessment and its files. */
export function DeleteAssessmentDialog({
  assessmentId,
  examName,
  fileCount,
}: DeleteAssessmentDialogProps) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string>();
  const [isDeleting, startDeleting] = useTransition();

  function handleDelete() {
    startDeleting(async () => {
      try {
        // Once deleted, the action opens the Assessments page instead of returning.
        const result = await deleteAssessment(assessmentId);
        setError(result.error);
      } catch (cause) {
        // Let Next.js carry out that redirect (or the one to the login page).
        unstable_rethrow(cause);
        console.error("Delete failed:", cause);
        setError(
          "The assessment couldn't be deleted. Check your connection and try again.",
        );
      }
    });
  }

  const files = `${fileCount} uploaded ${fileCount === 1 ? "file" : "files"}`;

  return (
    <AlertDialog
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        setError(undefined);
      }}
    >
      <AlertDialogTrigger render={<Button variant="outline" />}>
        <Trash2 />
        Delete
      </AlertDialogTrigger>
      <AlertDialogContent className="workspace-theme">
        <AlertDialogHeader>
          <AlertDialogTitle>
            {examName.trim()
              ? `Delete ${examName.trim()}?`
              : "Delete this untitled assessment?"}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {fileCount > 0
              ? `This permanently deletes the assessment and its ${files}. This can't be undone.`
              : "This permanently deletes the assessment. This can't be undone."}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={handleDelete}
            disabled={isDeleting}
          >
            {isDeleting && <Spinner />}
            Delete assessment
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
