"use client";

import { useId } from "react";

import { OptionGroup, type Option } from "@/components/assessment/option-group";
import { FieldError, FieldLegend, FieldSet } from "@/components/ui/field";
import { DIFFICULTIES, DIFFICULTY_LABELS, type Difficulty } from "@/lib/assessments/draft";

const OPTIONS: Option<Difficulty | "none">[] = [
  { value: "none", label: "Not specified" },
  ...DIFFICULTIES.map((difficulty) => ({ value: difficulty, label: DIFFICULTY_LABELS[difficulty] })),
];

type DifficultySelectorProps = {
  /** null when no difficulty is specified. */
  value: Difficulty | null;
  onChange: (value: Difficulty | null) => void;
  error?: string;
};

export function DifficultySelector({ value, onChange, error }: DifficultySelectorProps) {
  const legendId = useId();

  return (
    <FieldSet data-invalid={error ? true : undefined}>
      <FieldLegend id={legendId} variant="label">
        Difficulty
      </FieldLegend>
      <OptionGroup
        labelledBy={legendId}
        options={OPTIONS}
        value={value ?? "none"}
        onChange={(choice) => onChange(choice === "none" ? null : choice)}
      />
      {error && <FieldError>{error}</FieldError>}
    </FieldSet>
  );
}
