"use client";

import { ArrowRight, CircleAlert, CircleCheck, Save } from "lucide-react";
import { unstable_rethrow, useRouter } from "next/navigation";
import { useEffect, useRef, useState, useTransition, type FormEvent } from "react";
import { flushSync } from "react-dom";

import { AdditionalNotes } from "@/components/assessment/additional-notes";
import { AssessmentBasics } from "@/components/assessment/assessment-basics";
import { AssessmentPrompt } from "@/components/assessment/assessment-prompt";
import { AssessmentSummary } from "@/components/assessment/assessment-summary";
import { DifficultySelector } from "@/components/assessment/difficulty-selector";
import { DocumentUploadSection } from "@/components/assessment/document-upload-section";
import {
  durationInMinutes,
  durationValue,
  ExamDuration,
  type DurationValue,
} from "@/components/assessment/exam-duration";
import { FormSection } from "@/components/assessment/form-section";
import {
  QuestionDistribution,
  type DistributionValue,
} from "@/components/assessment/question-distribution";
import { useAssessmentDraft } from "@/components/assessment/use-assessment-draft";
import { useDocumentUploads } from "@/components/assessment/use-document-uploads";
import {
  versionCount,
  VersionSelector,
  versionValue,
  type VersionValue,
} from "@/components/assessment/version-selector";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import {
  saveAssessmentDraft,
  type DraftLocation,
  type SaveDraftResult,
} from "@/lib/assessments/actions";
import {
  validateAssessmentDraft,
  type AssessmentDraft,
  type Difficulty,
} from "@/lib/assessments/draft";
import type { Course } from "@/lib/courses/queries";
import type { DocumentFile } from "@/lib/documents/files";

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

/** The reverse of toDraft, to reopen a saved draft. */
function toFormValues(draft: AssessmentDraft): FormValues {
  return {
    courseId: draft.courseId,
    examName: draft.examName,
    duration: durationValue(draft.durationMinutes),
    // Without a saved split, the slider starts at 50/50 when it's switched on.
    distribution:
      draft.mcqPercentage === null
        ? { enabled: false, mcqPercentage: 50 }
        : { enabled: true, mcqPercentage: draft.mcqPercentage },
    versions: versionValue(draft.numberOfVersions),
    difficulty: draft.difficulty,
    additionalNotes: draft.additionalNotes,
    professorPrompt: draft.professorPrompt,
  };
}

/** A saved draft, reopened for editing. */
export type SavedAssessment = DraftLocation & {
  draft: AssessmentDraft;
  files: DocumentFile[];
};

// A new, untouched form has nothing to save.
const EMPTY_SNAPSHOT = JSON.stringify(toDraft(EMPTY_FORM));

const assessmentPath = (id: string) => `/workspace/assessments/${id}`;

type AssessmentFormProps = {
  courses: Course[];
  /** The draft to edit. Left out when creating a new assessment. */
  saved?: SavedAssessment;
};

export function AssessmentForm({ courses, saved }: AssessmentFormProps) {
  const router = useRouter();
  const formRef = useRef<HTMLFormElement>(null);
  const [values, setValues] = useState(() => (saved ? toFormValues(saved.draft) : EMPTY_FORM));
  // The draft as it was last saved (or opened), to tell whether anything changed since.
  const [savedSnapshot, setSavedSnapshot] = useState(() =>
    saved ? JSON.stringify(toDraft(toFormValues(saved.draft))) : null,
  );
  // Field errors appear after the first attempt to save, then update while editing.
  const [showErrors, setShowErrors] = useState(false);
  const [saveResult, setSaveResult] = useState<SaveDraftResult>({});
  const [isSaving, startSaving] = useTransition();
  const [savingFor, setSavingFor] = useState<"stay" | "continue">("stay");
  // Stops a quick second click from saving twice.
  const saveInProgress = useRef(false);

  const getDraft = useAssessmentDraft(saved ?? null, {
    courseId: values.courseId,
    examName: values.examName,
  });
  const uploads = useDocumentUploads(saved?.files ?? [], getDraft);

  const draft = toDraft(values);
  const clientErrors = validateAssessmentDraft(draft);
  const errors = showErrors ? { ...clientErrors, ...saveResult.fieldErrors } : {};
  const course = courses.find((candidate) => candidate.id === values.courseId);
  const hasUnsavedChanges = JSON.stringify(draft) !== (savedSnapshot ?? EMPTY_SNAPSHOT);
  const saveFailed = Boolean(saveResult.error || saveResult.fieldErrors);
  // A new assessment needs its course and name before its draft can hold files.
  const uploadsDisabledReason =
    saved || (values.courseId && values.examName.trim())
      ? undefined
      : "Choose a course and enter the assessment name above to add files.";

  // Ask before the page is reloaded or closed while changes or uploads would be lost.
  const shouldWarnBeforeLeaving = hasUnsavedChanges || uploads.isBusy;
  useEffect(() => {
    if (!shouldWarnBeforeLeaving) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [shouldWarnBeforeLeaving]);

  function update(changes: Partial<FormValues>) {
    setValues((current) => ({ ...current, ...changes }));
    // A problem reported by the last save no longer applies once something changes.
    setSaveResult({});
  }

  /** Saves the form, creating the draft first if this is a new assessment. */
  async function saveDraft(): Promise<SaveDraftResult & { examProjectId?: string }> {
    try {
      const { draft: location, error } = await getDraft();
      if (!location) return { error: error ?? "Your draft couldn't be saved. Please try again." };
      const result = await saveAssessmentDraft(location.examProjectId, draft);
      if (result.error || result.fieldErrors) return result;
      return { examProjectId: location.examProjectId };
    } catch (error) {
      // For example, the session ended: Next.js then opens the login page.
      unstable_rethrow(error);
      console.error("Save failed:", error);
      return { error: "Your draft couldn't be saved. Check your connection and try again." };
    }
  }

  function save(then: "stay" | "continue") {
    if (saveInProgress.current || uploads.isBusy) return;

    if (Object.keys(clientErrors).length > 0) {
      // Show the errors now, then move focus to the first field that needs attention.
      flushSync(() => setShowErrors(true));
      formRef.current?.querySelector<HTMLElement>("[aria-invalid='true']:not(:disabled)")?.focus();
      return;
    }
    setShowErrors(true);

    // Nothing changed since the draft was saved, so there's nothing to save again.
    if (then === "continue" && saved && !hasUnsavedChanges) {
      router.push(assessmentPath(saved.examProjectId));
      return;
    }

    saveInProgress.current = true;
    setSavingFor(then);
    const snapshot = JSON.stringify(draft);
    startSaving(async () => {
      const result = await saveDraft();
      saveInProgress.current = false;
      startSaving(() => {
        const { examProjectId } = result;
        if (!examProjectId) {
          setSaveResult(result);
          return;
        }
        setSaveResult({});
        setSavedSnapshot(snapshot);
        if (then === "continue") {
          // For a new assessment, the overview replaces the "new" page in the
          // history, so Back doesn't return to an empty form.
          if (saved) router.push(assessmentPath(examProjectId));
          else router.replace(assessmentPath(examProjectId));
        } else if (!saved) {
          // A new assessment moves to its own edit address, so reloading the page reopens it.
          router.replace(`${assessmentPath(examProjectId)}/edit`, { scroll: false });
        }
      });
    });
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    // React also delivers the New course dialog's submit here, because events
    // bubble through portals. That form handles itself.
    if (event.target !== event.currentTarget) return;
    event.preventDefault();
    // Pressing Enter in a field saves the draft without leaving the page.
    save("stay");
  }

  return (
    <form
      ref={formRef}
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

        <DocumentUploadSection
          title="Course material"
          description="Upload lectures, notes, readings, assignments, or other material the assessment should be based on."
          category="course_material"
          uploads={uploads}
          disabledReason={uploadsDisabledReason}
        />

        <DocumentUploadSection
          title="Previous assessments"
          description="Optional. Upload previous exams, quizzes, or answer keys so ProfPilot can later understand your assessment style and difficulty."
          category="previous_exam"
          uploads={uploads}
          disabledReason={uploadsDisabledReason}
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

        <DocumentUploadSection
          title="Additional images or attachments"
          description="Optional. Add screenshots, diagrams, graphs, tables, or other images you may want the assessment to reference."
          category="additional_attachment"
          uploads={uploads}
          disabledReason={uploadsDisabledReason}
        />

        <AssessmentPrompt
          value={values.professorPrompt}
          onChange={(professorPrompt) => update({ professorPrompt })}
          error={errors.professorPrompt}
        />
      </div>

      <AssessmentSummary
        draft={draft}
        course={course}
        files={uploads.items}
        className="lg:sticky lg:top-10"
      >
        <Button
          type="button"
          size="lg"
          className="w-full"
          disabled={isSaving || uploads.isBusy}
          onClick={() => save("continue")}
        >
          Continue
          {isSaving && savingFor === "continue" ? (
            <Spinner data-icon="inline-end" />
          ) : (
            <ArrowRight data-icon="inline-end" />
          )}
        </Button>
        {/* The form's submit button, so pressing Enter in a field saves too. */}
        <Button
          type="submit"
          variant="outline"
          size="lg"
          className="w-full"
          disabled={isSaving || uploads.isBusy}
        >
          {isSaving && savingFor === "stay" ? <Spinner /> : <Save />}
          {isSaving && savingFor === "stay" ? "Saving…" : "Save draft"}
        </Button>

        <p role="status" className="flex min-h-5 items-center justify-center gap-1.5 text-sm">
          {isSaving ? (
            <span className="text-muted-foreground">Saving…</span>
          ) : saveFailed ? (
            <span className="inline-flex items-center gap-1.5 font-medium text-destructive">
              <CircleAlert className="size-4" />
              Save failed
            </span>
          ) : savedSnapshot !== null && !hasUnsavedChanges ? (
            <span className="inline-flex items-center gap-1.5 text-muted-foreground">
              <CircleCheck className="size-4 text-emerald-600 dark:text-emerald-400" />
              Saved
            </span>
          ) : savedSnapshot !== null ? (
            <span className="text-muted-foreground">Unsaved changes</span>
          ) : null}
        </p>
        {saveResult.error && (
          <Alert variant="destructive">
            <AlertDescription>{saveResult.error}</AlertDescription>
          </Alert>
        )}
        <p className="text-xs text-muted-foreground">
          {uploads.isBusy
            ? "You can save once your files finish uploading."
            : "Continue saves your draft and opens its overview. Generating the exam isn't available yet."}
        </p>
      </AssessmentSummary>
    </form>
  );
}
