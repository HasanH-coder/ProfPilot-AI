"use client";

import { useId } from "react";

import { OptionGroup, type Option } from "@/components/assessment/option-group";
import { FieldError, FieldLegend, FieldSet } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { DURATION_PRESETS, LIMITS } from "@/lib/assessments/draft";

type DurationChoice = "none" | `${(typeof DURATION_PRESETS)[number]}` | "custom";

export type DurationValue = {
  choice: DurationChoice;
  /** What was typed in the Custom box, kept even while another option is chosen. */
  customMinutes: string;
};

const OPTIONS: Option<DurationChoice>[] = [
  { value: "none", label: "Not specified" },
  ...DURATION_PRESETS.map((minutes) => ({
    value: `${minutes}` as DurationChoice,
    label: `${minutes} min`,
  })),
  { value: "custom", label: "Custom" },
];

/** The duration in minutes, null when not specified (NaN when the custom value isn't a number). */
export function durationInMinutes({ choice, customMinutes }: DurationValue): number | null {
  if (choice === "none") return null;
  if (choice === "custom") return customMinutes.trim() ? Number(customMinutes) : Number.NaN;
  return Number(choice);
}

/** The reverse, for a saved draft: a preset when one matches, otherwise Custom. */
export function durationValue(minutes: number | null): DurationValue {
  if (minutes === null) return { choice: "none", customMinutes: "" };
  const preset = DURATION_PRESETS.find((presetMinutes) => presetMinutes === minutes);
  return preset
    ? { choice: `${preset}`, customMinutes: "" }
    : { choice: "custom", customMinutes: `${minutes}` };
}

type ExamDurationProps = {
  value: DurationValue;
  onChange: (value: DurationValue) => void;
  error?: string;
};

export function ExamDuration({ value, onChange, error }: ExamDurationProps) {
  const legendId = useId();
  const customId = useId();
  const errorId = useId();

  return (
    <FieldSet data-invalid={error ? true : undefined}>
      <FieldLegend id={legendId} variant="label">
        Duration
      </FieldLegend>
      <OptionGroup
        labelledBy={legendId}
        options={OPTIONS}
        value={value.choice}
        onChange={(choice) => onChange({ ...value, choice })}
      />
      {value.choice === "custom" && (
        <div className="flex items-center gap-2">
          <Input
            id={customId}
            type="number"
            inputMode="numeric"
            min={1}
            max={LIMITS.durationMinutes}
            step={1}
            className="w-24"
            // Appears right after choosing Custom, so the professor can type straight away.
            autoFocus
            value={value.customMinutes}
            onChange={(event) => onChange({ ...value, customMinutes: event.target.value })}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
          />
          <Label htmlFor={customId} className="font-normal text-muted-foreground">
            minutes
          </Label>
        </div>
      )}
      {error && <FieldError id={errorId}>{error}</FieldError>}
    </FieldSet>
  );
}
