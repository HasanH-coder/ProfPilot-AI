"use client";

import { useId } from "react";

import { FormSection } from "@/components/assessment/form-section";
import { Field, FieldError } from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";
import { LIMITS } from "@/lib/assessments/draft";

// Example text only; the field always starts empty.
const PLACEHOLDER = [
  "e.g. Focus more heavily on Lectures 4 and 5.",
  "Do not include AWS services.",
  "Include one long scenario question.",
].join("\n");

type AdditionalNotesProps = {
  value: string;
  onChange: (value: string) => void;
  error?: string;
};

export function AdditionalNotes({ value, onChange, error }: AdditionalNotesProps) {
  const textareaId = useId();
  const descriptionId = useId();
  const errorId = useId();

  return (
    <FormSection
      title="Additional notes"
      description="Optional. Specific requests or constraints for this assessment."
      descriptionId={descriptionId}
      labelFor={textareaId}
    >
      <Field data-invalid={error ? true : undefined}>
        <Textarea
          id={textareaId}
          className="min-h-28"
          maxLength={LIMITS.additionalNotes}
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
