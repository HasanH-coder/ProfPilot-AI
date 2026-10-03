"use client";

import { Save } from "lucide-react";
import { useState, useTransition, type FormEvent } from "react";
import { flushSync } from "react-dom";

import { AdditionalNotes } from "@/components/assessment/additional-notes";
import { AssessmentBasics } from "@/components/assessment/assessment-basics";
import { AssessmentPrompt } from "@/components/assessment/assessment-prompt";
import { AssessmentSummary } from "@/components/assessment/assessment-summary";
import { DifficultySelector } from "@/components/assessment/difficulty-selector";
import {
  durationInMinutes,
  ExamDuration,
  type DurationValue,
} from "@/components/assessment/exam-duration";
import { FormSection } from "@/components/assessment/form-section";
import {
  QuestionDistribution,
  type DistributionValue,
} from "@/components/assessment/question-distribution";
import {
  versionCount,
  VersionSelector,
  type VersionValue,
} from "@/components/assessment/version-selector";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { saveAssessmentDraft, type SaveDraftResult } from "@/lib/assessments/actions";
import {
  validateAssessmentDraft,
  type AssessmentDraft,
  type Difficulty,
} from "@/lib/assessments/draft";
import type { Course } from "@/lib/courses/queries";

/** Everything the professor has entered, including UI choices such as "Custom". */
type FormValues = {
  courseId: string | null;
  examName: string;
  duration: DurationValue;
  distribution: DistributionValue;
  versions: VersionValue;
  difficulty: Difficulty | null;
  additionalNotes: string;
  professorPrompt: string;
};

const EMPTY_FORM: FormValues = {
  courseId: null,
  examName: "",
  duration: { choice: "none", customMinutes: "" },
  distribution: { enabled: false, mcqPercentage: 50 },
  versions: { choice: "1", customCount: "" },
  difficulty: null,
  additionalNotes: "",
  professorPrompt: "",
};

/** Turns the form into the draft that is validated and saved. */
function toDraft(values: FormValues): AssessmentDraft {
  const { distribution } = values;
  return {
    courseId: values.courseId,
    examName: values.examName,
    durationMinutes: durationInMinutes(values.duration),
    mcqPercentage: distribution.enabled ? distribution.mcqPercentage : null,
    subjectivePercentage: distribution.enabled ? 100 - distribution.mcqPercentage : null,
    numberOfVersions: versionCount(values.versions),
    difficulty: values.difficulty,
    additionalNotes: values.additionalNotes,
    professorPrompt: values.professorPrompt,
  };
}

export function AssessmentForm({ courses }: { courses: Course[] }) {
  const [values, setValues] = useState(EMPTY_FORM);
  // Field errors appear after the first attempt to save, then update while editing.
  const [showErrors, setShowErrors] = useState(false);
  const [saveResult, setSaveResult] = useState<SaveDraftResult>({});
  const [isSaving, startSaving] = useTransition();

  const draft = toDraft(values);
  const clientErrors = validateAssessmentDraft(draft);
  const errors = showErrors ? { ...clientErrors, ...saveResult.fieldErrors } : {};
  const course = courses.find((candidate) => candidate.id === values.courseId);

  function update(changes: Partial<FormValues>) {
    setValues((current) => ({ ...current, ...changes }));
    // A problem reported by the last save no longer applies once something changes.
    setSaveResult({});
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    // React also delivers the New course dialog's submit here, because events
    // bubble through portals. That form handles itself.
    if (event.target !== event.currentTarget) return;
    event.preventDefault();
    const form = event.currentTarget;

    if (Object.keys(clientErrors).length > 0) {
      // Show the errors now, then move focus to the first field that needs attention.
      flushSync(() => setShowErrors(true));
      form.querySelector<HTMLElement>("[aria-invalid='true']:not(:disabled)")?.focus();
      return;
    }

    setShowErrors(true);
    startSaving(async () => {
      // When the draft is saved, the action opens the Assessments page instead of returning.
      const result = await saveAssessmentDraft(draft);
      if (result) setSaveResult(result);
    });
  }

  return (
    <form
      onSubmit={handleSubmit}
      noValidate
      className="grid items-start gap-12 lg:grid-cols-[minmax(0,1fr)_17rem] lg:gap-10 xl:grid-cols-[minmax(0,1fr)_19rem]"
    >
      <div className="flex min-w-0 flex-col gap-12">
        <AssessmentBasics
          courses={courses}
          courseId={values.courseId}
          onCourseChange={(courseId) => update({ courseId })}
          examName={values.examName}
          onExamNameChange={(examName) => update({ examName })}
          errors={errors}
        />

        <FormSection
          title="Exam design"
          description="Set what you already know. Everything here is optional."
        >
          <ExamDuration
            value={values.duration}
            onChange={(duration) => update({ duration })}
            error={errors.durationMinutes}
          />
          <QuestionDistribution
            value={values.distribution}
            onChange={(distribution) => update({ distribution })}
            error={errors.distribution}
          />
          <VersionSelector
            value={values.versions}
            onChange={(versions) => update({ versions })}
            error={errors.numberOfVersions}
          />
          <DifficultySelector
            value={values.difficulty}
            onChange={(difficulty) => update({ difficulty })}
            error={errors.difficulty}
          />
        </FormSection>

        <AdditionalNotes
          value={values.additionalNotes}
          onChange={(additionalNotes) => update({ additionalNotes })}
          error={errors.additionalNotes}
        />

        <AssessmentPrompt
          value={values.professorPrompt}
          onChange={(professorPrompt) => update({ professorPrompt })}
          error={errors.professorPrompt}
        />
      </div>

      <AssessmentSummary draft={draft} course={course} className="lg:sticky lg:top-10">
        {saveResult.error && (
          <Alert variant="destructive">
            <AlertDescription>{saveResult.error}</AlertDescription>
          </Alert>
        )}
        <Button type="submit" size="lg" className="w-full" disabled={isSaving}>
          {isSaving ? <Spinner /> : <Save />}
          Save draft
        </Button>
        <p className="text-xs text-muted-foreground">
          Saved to your workspace as a draft. Generating the exam isn&apos;t available yet.
        </p>
      </AssessmentSummary>
    </form>
  );
}
