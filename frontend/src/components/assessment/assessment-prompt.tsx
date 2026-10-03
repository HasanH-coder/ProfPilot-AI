"use client";

import { useId } from "react";

import { FormSection } from "@/components/assessment/form-section";
import { Field, FieldError } from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";
import { LIMITS } from "@/lib/assessments/draft";

// Example text only; the field always starts empty.
const PLACEHOLDER =
  "e.g. Create a challenging midterm mainly based on Lectures 3–5. I want more application questions than memorization, with two scenario-based subjective questions. Keep it around one hour and make two equivalent versions.";

type AssessmentPromptProps = {
  value: string;
  onChange: (value: string) => void;
  error?: string;
};

/** The professor's instructions in their own words, saved exactly as written. */
export function AssessmentPrompt({ value, onChange, error }: AssessmentPromptProps) {
  const textareaId = useId();
  const descriptionId = useId();
  const errorId = useId();

  return (
    <FormSection
      title="Tell ProfPilot what you want"
      description="You can write naturally. Anything you leave unspecified above can be described here."
      descriptionId={descriptionId}
      labelFor={textareaId}
    >
      <Field data-invalid={error ? true : undefined}>
        <Textarea
          id={textareaId}
          className="min-h-44"
          maxLength={LIMITS.professorPrompt}
          placeholder={PLACEHOLDER}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${descriptionId} ${errorId}` : descriptionId}
        />
        {error && <FieldError id={errorId}>{error}</FieldError>}
      </Field>
    </FormSection>
  );
}
