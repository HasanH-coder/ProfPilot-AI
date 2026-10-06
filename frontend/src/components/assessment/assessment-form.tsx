"use client";

import {
  ArrowRight,
  BookOpenCheck,
  CircleAlert,
  CircleCheck,
  Save,
} from "lucide-react";
import { unstable_rethrow, useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useTransition,
  type FormEvent,
  type ReactNode,
} from "react";
import { flushSync } from "react-dom";

import { AdditionalNotes } from "@/components/assessment/additional-notes";
import { AssessmentHelp } from "@/components/assessment/assessment-help";
import { AssessmentBasics } from "@/components/assessment/assessment-basics";
import { AssessmentPrompt } from "@/components/assessment/assessment-prompt";
import { AssessmentSummary } from "@/components/assessment/assessment-summary";
import {
  DIFFICULTY_NOT_SPECIFIED,
  DifficultyDistribution,
  difficultyDistributionValue,
  difficultyPercentages,
  type DifficultyDistributionValue,
} from "@/components/assessment/difficulty-distribution";
import { DocumentUploadSection } from "@/components/assessment/document-upload-section";
import {
  durationInMinutes,
  durationValue,
  ExamDuration,
  type DurationValue,
} from "@/components/assessment/exam-duration";
import { FormSection } from "@/components/assessment/form-section";
import { ImproveWithAi } from "@/components/assessment/improve-with-ai";
import {
  QuestionDistribution,
  type DistributionValue,
} from "@/components/assessment/question-distribution";
import { SetupAssistant } from "@/components/assessment/setup-assistant";
import { useAssessmentDraft } from "@/components/assessment/use-assessment-draft";
import { useDocumentAnalysis } from "@/components/assessment/use-document-analysis";
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
} from "@/lib/assessments/draft";
import type { Course } from "@/lib/courses/queries";
import type { DocumentFile } from "@/lib/documents/files";
import { cn } from "@/lib/utils";

/** Everything the professor has entered, including UI choices such as "Custom". */
type FormValues = {
  courseId: string | null;
  examName: string;
  duration: DurationValue;
  distribution: DistributionValue;
  versions: VersionValue;
  difficulty: DifficultyDistributionValue;
  additionalNotes: string;
  professorPrompt: string;
};

const EMPTY_FORM: FormValues = {
  courseId: null,
  examName: "",
  duration: { choice: "none", customMinutes: "" },
  distribution: { enabled: false, mcqPercentage: 50 },
  versions: { choice: "1", customCount: "" },
  difficulty: DIFFICULTY_NOT_SPECIFIED,
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
    subjectivePercentage: distribution.enabled
      ? 100 - distribution.mcqPercentage
      : null,
    numberOfVersions: versionCount(values.versions),
    ...difficultyPercentages(values.difficulty),
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
    difficulty: difficultyDistributionValue(draft),
    additionalNotes: draft.additionalNotes,
    professorPrompt: draft.professorPrompt,
  };
}

/** Which part of the form each draft field belongs to (for highlighting changes). */
const FIELD_GROUPS: Record<string, string> = {
  courseId: "basics",
  examName: "basics",
  durationMinutes: "duration",
  mcqPercentage: "distribution",
  subjectivePercentage: "distribution",
  numberOfVersions: "versions",
  easyPercentage: "difficulty",
  mediumPercentage: "difficulty",
  hardPercentage: "difficulty",
  additionalNotes: "notes",
  professorPrompt: "prompt",
};

/**
 * Puts changes from the AI assistant into the form, one part at a time, so
 * anything else the professor is editing at that moment is left alone.
 */
function applyChanges(
  current: FormValues,
  setup: AssessmentDraft,
  fields: Set<string>,
): FormValues {
  const next = { ...current };
  if (fields.has("courseId")) next.courseId = setup.courseId;
  if (fields.has("examName")) next.examName = setup.examName;
  if (fields.has("durationMinutes"))
    next.duration = durationValue(setup.durationMinutes);
  if (fields.has("mcqPercentage") || fields.has("subjectivePercentage")) {
    next.distribution =
      setup.mcqPercentage === null
        ? { ...current.distribution, enabled: false }
        : { enabled: true, mcqPercentage: setup.mcqPercentage };
  }
  if (fields.has("numberOfVersions"))
    next.versions = versionValue(setup.numberOfVersions);
  if (
    fields.has("easyPercentage") ||
    fields.has("mediumPercentage") ||
    fields.has("hardPercentage")
  ) {
    next.difficulty =
      setup.easyPercentage === null
        ? { ...current.difficulty, enabled: false }
        : difficultyDistributionValue(setup);
  }
  if (fields.has("additionalNotes"))
    next.additionalNotes = setup.additionalNotes;
  if (fields.has("professorPrompt"))
    next.professorPrompt = setup.professorPrompt;
  return next;
}

/** A saved draft, reopened for editing. */
export type SavedAssessment = DraftLocation & {
  draft: AssessmentDraft;
  files: DocumentFile[];
};

// A new, untouched form has nothing to save.
const EMPTY_SNAPSHOT = JSON.stringify(toDraft(EMPTY_FORM));

const assessmentPath = (id: string) => `/workspace/assessments/${id}`;

// The first save of a new assessment reopens the form at the draft's own
// address. This flag lets the reopened form give keyboard focus back to Save draft.
let refocusSaveButton = false;

type AssessmentFormProps = {
  courses: Course[];
  /** The draft to edit. Left out when creating a new assessment. */
  saved?: SavedAssessment;
  initialAssistantOpen?: boolean;
};

export function AssessmentForm({
  courses,
  saved,
  initialAssistantOpen = false,
}: AssessmentFormProps) {
  const router = useRouter();
  const formRef = useRef<HTMLFormElement>(null);
  const saveButtonRef = useRef<HTMLButtonElement>(null);
  const [values, setValues] = useState(() =>
    saved ? toFormValues(saved.draft) : EMPTY_FORM,
  );
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
  const [assistantOpen, setAssistantOpen] = useState(initialAssistantOpen);
  // While a voice call runs, saving must never reload the page (that would end the call).
  const callActive = useRef(false);
  const [highlighted, setHighlighted] = useState<Set<string>>(() => new Set());
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const autosaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The latest values, for callbacks that run later (voice tools, autosave).
  const latestValues = useRef(values);
  useEffect(() => {
    latestValues.current = values;
  });
  // The draft's id, once it exists (a new assessment gets one on its first upload or save).
  const [draftId, setDraftId] = useState<string | null>(
    saved?.examProjectId ?? null,
  );

  const getDraftLocation = useAssessmentDraft(saved ?? null, {
    courseId: values.courseId,
    examName: values.examName,
  });
  const getDraft = useCallback(async () => {
    const result = await getDraftLocation();
    if (result.draft) setDraftId(result.draft.examProjectId);
    return result;
  }, [getDraftLocation]);
  const uploads = useDocumentUploads(saved?.files ?? [], getDraft);
  const uploadedIds = uploads.items.flatMap((item) =>
    item.documentId ? [item.documentId] : [],
  );
  const analysis = useDocumentAnalysis(draftId, uploadedIds);

  const draft = toDraft(values);
  const clientErrors = validateAssessmentDraft(draft);
  const errors = showErrors
    ? { ...clientErrors, ...saveResult.fieldErrors }
    : {};
  const course = courses.find((candidate) => candidate.id === values.courseId);
  const hasUnsavedChanges =
    JSON.stringify(draft) !== (savedSnapshot ?? EMPTY_SNAPSHOT);
  const saveFailed = Boolean(saveResult.error || saveResult.fieldErrors);

  useEffect(() => {
    if (!refocusSaveButton) return;
    refocusSaveButton = false;
    if (document.activeElement === document.body)
      saveButtonRef.current?.focus();
  }, []);

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
  async function saveDraft(
    target: AssessmentDraft = draft,
  ): Promise<SaveDraftResult & { examProjectId?: string }> {
    try {
      const { draft: location, error } = await getDraft();
      if (!location)
        return {
          error: error ?? "Your draft couldn't be saved. Please try again.",
        };
      const result = await saveAssessmentDraft(location.examProjectId, target);
      if (result.error || result.fieldErrors) return result;
      return { examProjectId: location.examProjectId };
    } catch (error) {
      // For example, the session ended: Next.js then opens the login page.
      unstable_rethrow(error);
      console.error("Save failed:", error);
      return {
        error:
          "Your draft couldn't be saved. Check your connection and try again.",
      };
    }
  }

  function save(then: "stay" | "continue") {
    if (saveInProgress.current || uploads.isBusy) return;

    if (Object.keys(clientErrors).length > 0) {
      // Show the errors now, then move focus to the first field that needs attention.
      flushSync(() => setShowErrors(true));
      formRef.current
        ?.querySelector<HTMLElement>("[aria-invalid='true']:not(:disabled)")
        ?.focus();
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
          if (callActive.current) {
            // Mid-call: change the address only, without reloading the form.
            window.history.replaceState(
              null,
              "",
              `${assessmentPath(examProjectId)}/edit`,
            );
          } else {
            refocusSaveButton = true;
            router.replace(`${assessmentPath(examProjectId)}/edit`, {
              scroll: false,
            });
          }
        }
      });
    });
  }

  /**
   * Saves without leaving the page (for the AI assistant and Improve with AI).
   * Returns the assessment's id, or null when the form has errors or saving failed.
   */
  async function persist(): Promise<string | null> {
    const current = toDraft(latestValues.current);
    if (Object.keys(validateAssessmentDraft(current)).length > 0) {
      flushSync(() => setShowErrors(true));
      formRef.current
        ?.querySelector<HTMLElement>("[aria-invalid='true']:not(:disabled)")
        ?.focus();
      return null;
    }
    const result = await saveDraft(current);
    if (!result.examProjectId) {
      setShowErrors(true);
      setSaveResult(result);
      return null;
    }
    setSaveResult({});
    setSavedSnapshot(JSON.stringify(current));
    // A new assessment gets its own address, so reloading reopens it (no reload now).
    if (!saved && !window.location.pathname.endsWith("/edit")) {
      window.history.replaceState(
        null,
        "",
        `${assessmentPath(result.examProjectId)}/edit`,
      );
    }
    return result.examProjectId;
  }

  /** Puts the assistant's changes into the form, shows them, and saves them shortly after. */
  function applyAssistantChanges(
    setup: AssessmentDraft,
    changedFields: string[],
  ) {
    const fields = new Set(changedFields);
    setValues((current) => applyChanges(current, setup, fields));
    setSaveResult({});
    setHighlighted(
      new Set(
        changedFields.map((field) => FIELD_GROUPS[field]).filter(Boolean),
      ),
    );
    if (highlightTimer.current) clearTimeout(highlightTimer.current);
    highlightTimer.current = setTimeout(() => setHighlighted(new Set()), 2500);
    if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    autosaveTimer.current = setTimeout(() => void persist(), 800);
  }

  // A highlight around the parts of the form the assistant just changed.
  const changedBy = (group: string) =>
    cn(
      "rounded-xl transition-shadow duration-700",
      highlighted.has(group) &&
        "ring-2 ring-primary/35 ring-offset-4 ring-offset-background",
    );

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
      className="assessment-form"
    >
      <div className="assessment-fields">
        {assistantOpen && (
          <SetupAssistant
            getSetup={() => toDraft(latestValues.current)}
            onApply={applyAssistantChanges}
            ensureDraft={async () =>
              (await getDraft()).draft?.examProjectId ?? null
            }
            onCallActiveChange={(active) => {
              callActive.current = active;
            }}
            onClose={() => setAssistantOpen(false)}
          />
        )}

        <div className={changedBy("basics")}>
          <FormSection title="Course & Assessment Details">
            <AssessmentBasics
              courses={courses}
              courseId={values.courseId}
              onCourseChange={(courseId) => update({ courseId })}
              examName={values.examName}
              onExamNameChange={(examName) => update({ examName })}
              errors={errors}
            />
          </FormSection>
        </div>

        <DocumentUploadSection
          title="Course material"
          description="Upload lectures, notes, readings, assignments, or any material the assessment should be based on."
          category="course_material"
          uploads={uploads}
          analysis={analysis.documents}
          onRetryAnalysis={analysis.retry}
        />
        <DocumentUploadSection
          title="Previous assessments"
          description="Optional. Upload previous exams, quizzes, or answer keys so ProfPilot can learn your assessment style and difficulty. They guide the style; they aren't copied."
          category="previous_exam"
          uploads={uploads}
          analysis={analysis.documents}
          onRetryAnalysis={analysis.retry}
          footer={
            analysis.styleProfile && (
              <StyleNote>
                ProfPilot analysed {analysis.styleProfile.examsAnalyzed}{" "}
                previous{" "}
                {analysis.styleProfile.examsAnalyzed === 1 ? "exam" : "exams"}:{" "}
                {analysis.styleProfile.summary}
              </StyleNote>
            )
          }
        />
        <FormSection
          title="Exam design"
          description="Optional. Set only what you already know."
        >
          <div className={changedBy("duration")}>
            <ExamDuration
              value={values.duration}
              onChange={(duration) => update({ duration })}
              error={errors.durationMinutes}
            />
          </div>
          <div className={changedBy("distribution")}>
            <QuestionDistribution
              value={values.distribution}
              onChange={(distribution) => update({ distribution })}
              error={errors.distribution}
            />
          </div>
          <div className={changedBy("versions")}>
            <VersionSelector
              value={values.versions}
              onChange={(versions) => update({ versions })}
              error={errors.numberOfVersions}
            />
          </div>
          <div className={changedBy("difficulty")}>
            <DifficultyDistribution
              value={values.difficulty}
              onChange={(difficulty) => update({ difficulty })}
              error={errors.difficultyDistribution}
            />
          </div>
        </FormSection>

        <div className={changedBy("notes")}>
          <AdditionalNotes
            value={values.additionalNotes}
            onChange={(additionalNotes) => update({ additionalNotes })}
            error={errors.additionalNotes}
          />
        </div>

        <DocumentUploadSection
          title="Additional images or attachments"
          description="Optional. Add screenshots, diagrams, graphs, tables, or other images you may want the assessment to reference."
          category="additional_attachment"
          uploads={uploads}
          analysis={analysis.documents}
          onRetryAnalysis={analysis.retry}
        />

        <div
          id="assessment-instructions"
          data-assessment-prompt
          className={cn("flex flex-col gap-4", changedBy("prompt"))}
        >
          <AssessmentPrompt
            value={values.professorPrompt}
            onChange={(professorPrompt) => update({ professorPrompt })}
            error={errors.professorPrompt}
          />
          <ImproveWithAi
            ensureSaved={persist}
            disabled={uploads.isBusy || isSaving}
          />
        </div>
      </div>
      <div className="assessment-aside">
        <AssessmentSummary
          draft={draft}
          course={course}
          files={uploads.items}
        />
        {saveResult.error && (
          <Alert variant="destructive">
            <AlertDescription>{saveResult.error}</AlertDescription>
          </Alert>
        )}
        <AssessmentHelp
          onOpenAssistant={() => {
            setAssistantOpen(true);
            requestAnimationFrame(() => {
              const panel = formRef.current?.querySelector<HTMLElement>(
                "[data-setup-assistant]",
              );
              panel?.scrollIntoView({
                block: "center",
                behavior: window.matchMedia("(prefers-reduced-motion: reduce)")
                  .matches
                  ? "instant"
                  : "smooth",
              });
              panel
                ?.querySelector<HTMLInputElement>("input")
                ?.focus({ preventScroll: true });
            });
          }}
        />
      </div>
      <div className="assessment-action-bar">
        <div className="assessment-action-state">
          <Button
            type="button"
            variant="outline"
            disabled={isSaving || uploads.isBusy}
            onClick={() => {
              if (
                !hasUnsavedChanges ||
                window.confirm(
                  "Leave this assessment? Unsaved form changes will be lost. Uploaded files remain saved.",
                )
              )
                router.push("/workspace/assessments");
            }}
          >
            Cancel
          </Button>
          <p role="status" className="flex items-center gap-1.5 text-sm">
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
            ) : (
              <span className="text-muted-foreground">
                {savedSnapshot !== null ? "Unsaved changes" : "Not saved yet"}
              </span>
            )}
          </p>
        </div>
        <div className="assessment-action-buttons">
          {/* Keep submit semantics: Enter in a field saves without leaving. */}
          <Button
            ref={saveButtonRef}
            type="submit"
            variant="outline"
            size="lg"
            disabled={isSaving || uploads.isBusy}
          >
            {isSaving && savingFor === "stay" ? <Spinner /> : <Save />}
            {isSaving && savingFor === "stay" ? "Saving…" : "Save draft"}
          </Button>
          <Button
            type="button"
            size="lg"
            disabled={isSaving || uploads.isBusy}
            title={
              uploads.isBusy
                ? "Wait for files to finish uploading"
                : "Save your draft and open its overview"
            }
            onClick={() => save("continue")}
          >
            Continue
            {isSaving && savingFor === "continue" ? (
              <Spinner data-icon="inline-end" />
            ) : (
              <ArrowRight data-icon="inline-end" />
            )}
          </Button>
        </div>
      </div>
    </form>
  );
}

/** A short note under a section, e.g. that previous exams were analysed. */
function StyleNote({ children }: { children: ReactNode }) {
  return (
    <p className="flex items-start gap-2 rounded-lg bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
      <BookOpenCheck className="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
      <span>{children}</span>
    </p>
  );
}
