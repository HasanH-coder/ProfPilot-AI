"use client";

import { useId } from "react";

import { OptionGroup, type Option } from "@/components/assessment/option-group";
import { FieldDescription, FieldError, FieldLegend, FieldSet } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LIMITS, VERSION_PRESETS } from "@/lib/assessments/draft";

type VersionChoice = `${(typeof VERSION_PRESETS)[number]}` | "custom";

export type VersionValue = {
  choice: VersionChoice;
  /** What was typed in the Custom box, kept even while another option is chosen. */
  customCount: string;
};

const OPTIONS: Option<VersionChoice>[] = [
  ...VERSION_PRESETS.map((count) => ({ value: `${count}` as VersionChoice, label: `${count}` })),
  { value: "custom", label: "Custom" },
];

/** The number of versions (NaN when the custom value isn't a number). */
export function versionCount({ choice, customCount }: VersionValue): number {
  if (choice === "custom") return customCount.trim() ? Number(customCount) : Number.NaN;
  return Number(choice);
}

/** The reverse, for a saved draft: a preset when one matches, otherwise Custom. */
export function versionValue(count: number): VersionValue {
  const preset = VERSION_PRESETS.find((presetCount) => presetCount === count);
  return preset
    ? { choice: `${preset}`, customCount: "" }
    : { choice: "custom", customCount: `${count}` };
}

type VersionSelectorProps = {
  value: VersionValue;
  onChange: (value: VersionValue) => void;
  error?: string;
};

export function VersionSelector({ value, onChange, error }: VersionSelectorProps) {
  const legendId = useId();
  const customId = useId();
  const errorId = useId();

  return (
    <FieldSet data-invalid={error ? true : undefined}>
      <FieldLegend id={legendId} variant="label">
        Number of versions
      </FieldLegend>
      <FieldDescription>Equivalent versions of the same exam.</FieldDescription>
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
            max={LIMITS.versions}
            step={1}
            className="w-24"
            // Appears right after choosing Custom, so the professor can type straight away.
            autoFocus
            value={value.customCount}
            onChange={(event) => onChange({ ...value, customCount: event.target.value })}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
          />
          <Label htmlFor={customId} className="font-normal text-muted-foreground">
            versions
          </Label>
        </div>
      )}
      {error && <FieldError id={errorId}>{error}</FieldError>}
    </FieldSet>
  );
}
