"use client";

import { Trash2 } from "lucide-react";
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
import { deleteCourse } from "@/lib/courses/actions";
import type { Course } from "@/lib/courses/queries";

/** A Delete button that asks for confirmation before deleting the course. */
export function DeleteCourseDialog({ course }: { course: Course }) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string>();
  const [isDeleting, startDeleting] = useTransition();

  function handleDelete() {
    startDeleting(async () => {
      const result = await deleteCourse(course.id);
      if (result.error) setError(result.error);
      else setOpen(false);
    });
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        setError(undefined);
      }}
    >
      <AlertDialogTrigger
        render={<Button variant="ghost" size="sm" aria-label={`Delete ${course.code}`} />}
      >
        <Trash2 />
        <span className="max-sm:sr-only">Delete</span>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete {course.code}?</AlertDialogTitle>
          <AlertDialogDescription>
            This removes the course from your workspace. Assessments created for it are kept,
            but they will no longer be linked to a course.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={handleDelete} disabled={isDeleting}>
            {isDeleting && <Spinner />}
            Delete course
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
