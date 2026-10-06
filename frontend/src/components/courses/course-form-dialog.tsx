"use client";

import { useActionState, useId, useState, type ReactElement } from "react";

import { FormField } from "@/components/form-field";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { FieldGroup } from "@/components/ui/field";
import { Spinner } from "@/components/ui/spinner";
import {
  createCourse,
  updateCourse,
  type CourseFormState,
} from "@/lib/courses/actions";
import type { Course } from "@/lib/courses/queries";

type CourseFormDialogProps = {
  /** The button that opens the dialog. */
  trigger: ReactElement;
  /** The course to edit. Leave it out to add a new course. */
  course?: Course;
  /** Called with the saved course, for example to select it in a form. */
  onSaved?: (course: Course) => void;
};

/** A dialog for adding a new course or editing an existing one. */
export function CourseFormDialog({
  trigger,
  course,
  onSaved,
}: CourseFormDialogProps) {
  const [open, setOpen] = useState(false);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={trigger} />
      <DialogContent className="workspace-theme sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{course ? "Edit course" : "New course"}</DialogTitle>
          <DialogDescription>
            {course
              ? "Update the course code or name."
              : "Add a course you teach. You can edit it later."}
          </DialogDescription>
        </DialogHeader>
        <CourseForm
          course={course}
          onSaved={(saved) => {
            setOpen(false);
            onSaved?.(saved);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}

// Rendered inside the dialog, so its fields start fresh every time the dialog opens.
function CourseForm({
  course,
  onSaved,
}: {
  course?: Course;
  onSaved: (course: Course) => void;
}) {
  const codeId = useId();
  const nameId = useId();
  // Controlled, so typed values survive the form reset React does after each submit.
  const [code, setCode] = useState(course?.code ?? "");
  const [name, setName] = useState(course?.name ?? "");
  const [state, formAction, isPending] = useActionState(
    async (previous: CourseFormState, formData: FormData) => {
      const result = course
        ? await updateCourse(course.id, previous, formData)
        : await createCourse(previous, formData);
      if (result.course) onSaved(result.course);
      return result;
    },
    {},
  );

  return (
    <form action={formAction} noValidate>
      <FieldGroup>
        {state.error && (
          <Alert variant="destructive">
            <AlertDescription>{state.error}</AlertDescription>
          </Alert>
        )}
        <FormField
          id={codeId}
          name="code"
          label="Course code"
          placeholder="e.g. CMPS 297U"
          autoComplete="off"
          required
          value={code}
          onChange={(event) => setCode(event.target.value)}
          error={state.fieldErrors?.code}
        />
        <FormField
          id={nameId}
          name="name"
          label="Course name (optional)"
          placeholder="e.g. Cloud Computing"
          autoComplete="off"
          value={name}
          onChange={(event) => setName(event.target.value)}
          error={state.fieldErrors?.name}
        />
      </FieldGroup>
      <DialogFooter className="mt-6">
        <DialogClose render={<Button variant="outline" />}>Cancel</DialogClose>
        <Button type="submit" disabled={isPending}>
          {isPending && <Spinner />}
          {course ? "Save changes" : "Add course"}
        </Button>
      </DialogFooter>
    </form>
  );
}
