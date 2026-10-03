"use client";

import { useId } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import { Field, FieldError, FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field";
import { Slider } from "@/components/ui/slider";

export type DistributionValue = {
  enabled: boolean;
  /** Kept while the distribution is switched off, so switching it back on restores it. */
  mcqPercentage: number;
};

type QuestionDistributionProps = {
  value: DistributionValue;
  onChange: (value: DistributionValue) => void;
  error?: string;
};

/** An optional split between MCQ and subjective questions that always adds up to 100%. */
export function QuestionDistribution({ value, onChange, error }: QuestionDistributionProps) {
  const checkboxId = useId();
  const sliderLabelId = useId();
  const errorId = useId();
  const subjectivePercentage = 100 - value.mcqPercentage;

  return (
    <FieldSet data-invalid={error ? true : undefined}>
      <FieldLegend variant="label">Question distribution</FieldLegend>
      <Field orientation="horizontal">
        <Checkbox
          id={checkboxId}
          checked={value.enabled}
          onCheckedChange={(enabled) => onChange({ ...value, enabled })}
        />
        <FieldLabel htmlFor={checkboxId} className="font-normal">
          Specify question distribution
        </FieldLabel>
      </Field>

      {value.enabled && (
        <div className="flex max-w-md flex-col gap-3">
          <div className="flex items-center justify-between text-sm">
            <span>
              MCQ <span className="font-medium tabular-nums">{value.mcqPercentage}%</span>
            </span>
            <span>
              Subjective <span className="font-medium tabular-nums">{subjectivePercentage}%</span>
            </span>
          </div>
          <span id={sliderLabelId} className="sr-only">
            Percentage of MCQ questions. Subjective questions make up the rest.
          </span>
          <Slider
            aria-labelledby={sliderLabelId}
            min={0}
            max={100}
            step={5}
            format={{ style: "unit", unit: "percent" }}
            value={[value.mcqPercentage]}
            onValueChange={(next) => {
              const mcqPercentage = Array.isArray(next) ? next[0] : next;
              onChange({ ...value, mcqPercentage });
            }}
          />
          <p className="text-xs text-muted-foreground">
            Subjective questions make up the rest, so the total is always 100%.
          </p>
        </div>
      )}
      {error && <FieldError id={errorId}>{error}</FieldError>}
    </FieldSet>
  );
}
