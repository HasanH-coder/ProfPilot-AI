"use client";

import { Plus } from "lucide-react";
import { useId } from "react";

import { CourseFormDialog } from "@/components/courses/course-form-dialog";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { LIMITS } from "@/lib/assessments/draft";
import type { Course } from "@/lib/courses/queries";

type AssessmentBasicsProps = {
  courses: Course[];
  courseId: string | null;
  onCourseChange: (courseId: string | null) => void;
  examName: string;
  onExamNameChange: (examName: string) => void;
  errors: { courseId?: string; examName?: string };
};

/** Which course the assessment is for (or a new one), and the assessment's name. Both are optional. */
export function AssessmentBasics({
  courses,
  courseId,
  onCourseChange,
  examName,
  onExamNameChange,
  errors,
}: AssessmentBasicsProps) {
  const courseFieldId = useId();
  const courseErrorId = useId();
  const examNameId = useId();
  // Tells the select which text to show for the chosen course.
  const courseLabels = Object.fromEntries(
    courses.map((course) => [course.id, courseLabel(course)]),
  );
  // Only set when the save is rejected, for example for a course that was just deleted.
  const courseError = errors.courseId;

  return (
    <div className="flex flex-col gap-4">
      <Field data-invalid={courseError ? true : undefined}>
        <FieldLabel htmlFor={courseFieldId}>Course</FieldLabel>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Select
            items={courseLabels}
            value={courseId}
            onValueChange={onCourseChange}
          >
            <SelectTrigger
              id={courseFieldId}
              className="w-full sm:flex-1"
              disabled={courses.length === 0}
              aria-invalid={courseError ? true : undefined}
              aria-describedby={courseError ? courseErrorId : undefined}
            >
              <SelectValue
                placeholder={
                  courses.length > 0 ? "No course selected" : "No courses yet"
                }
              />
            </SelectTrigger>
            <SelectContent className="workspace-theme">
              {/* Clears the choice: an assessment doesn't need a course. */}
              <SelectItem value={null}>No course</SelectItem>
              {courses.map((course) => (
                <SelectItem key={course.id} value={course.id}>
                  {courseLabel(course)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <CourseFormDialog
            trigger={
              // text-foreground: keep the button's normal colour when the course field shows an error.
              <Button variant="outline" className="assessment-new-course">
                <Plus />
                New course
              </Button>
            }
            onSaved={(course) => onCourseChange(course.id)}
          />
        </div>
        {courseError && (
          <FieldError id={courseErrorId}>{courseError}</FieldError>
        )}
      </Field>

      <FormField
        id={examNameId}
        label="Assessment name"
        placeholder="e.g. Midterm, Final Exam, Quiz 2"
        autoComplete="off"
        maxLength={LIMITS.examName}
        value={examName}
        onChange={(event) => onExamNameChange(event.target.value)}
        error={errors.examName}
      />
    </div>
  );
}

function courseLabel(course: Course) {
  return course.name ? `${course.code} · ${course.name}` : course.code;
}
