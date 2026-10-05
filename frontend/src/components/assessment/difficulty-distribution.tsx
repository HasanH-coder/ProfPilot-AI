"use client";

import { CircleAlert, CircleCheck } from "lucide-react";
import { useId } from "react";

import { DifficultyBar, DifficultySwatch } from "@/components/assessment/difficulty-bar";
import { Checkbox } from "@/components/ui/checkbox";
import { Field, FieldDescription, FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  checkDifficultyDistribution,
  DEFAULT_DIFFICULTY_DISTRIBUTION,
  DIFFICULTIES,
  DIFFICULTY_LABELS,
  type Difficulty,
  type DifficultyPercentages,
} from "@/lib/assessments/draft";
import { cn } from "@/lib/utils";

export type DifficultyDistributionValue = {
  enabled: boolean;
  /** What is typed in each box, kept while the distribution is switched off, so switching it back on restores it. */
  percentages: Record<Difficulty, string>;
};

/** Switched off. Switching it on starts the boxes at 30 / 40 / 30. */
export const DIFFICULTY_NOT_SPECIFIED: DifficultyDistributionValue = {
  enabled: false,
  percentages: {
    easy: `${DEFAULT_DIFFICULTY_DISTRIBUTION.easy}`,
    medium: `${DEFAULT_DIFFICULTY_DISTRIBUTION.medium}`,
    hard: `${DEFAULT_DIFFICULTY_DISTRIBUTION.hard}`,
  },
};

/** The percentages to save: all null when not specified (NaN for a box that isn't a number). */
export function difficultyPercentages({ enabled, percentages }: DifficultyDistributionValue): DifficultyPercentages {
  const toNumber = (text: string) => (!enabled ? null : text.trim() ? Number(text) : Number.NaN);
  return {
    easyPercentage: toNumber(percentages.easy),
    mediumPercentage: toNumber(percentages.medium),
    hardPercentage: toNumber(percentages.hard),
  };
}

/** The reverse, for a saved draft. */
export function difficultyDistributionValue(draft: DifficultyPercentages): DifficultyDistributionValue {
  const { easyPercentage: easy, mediumPercentage: medium, hardPercentage: hard } = draft;
  if (easy === null || medium === null || hard === null) return DIFFICULTY_NOT_SPECIFIED;
  return { enabled: true, percentages: { easy: `${easy}`, medium: `${medium}`, hard: `${hard}` } };
}

type DifficultyDistributionProps = {
  value: DifficultyDistributionValue;
  onChange: (value: DifficultyDistributionValue) => void;
  /** Set after an attempt to save an invalid distribution. */
  error?: string;
};

/**
 * An optional split of the assessment into easy, medium and hard, typed as
 * three percentages. Nothing is adjusted automatically: while they don't add
 * up to 100%, a message under the boxes says so, and the draft can't be saved.
 */
export function DifficultyDistribution({ value, onChange, error }: DifficultyDistributionProps) {
  const checkboxId = useId();
  const totalId = useId();
  const inputIds: Record<Difficulty, string> = { easy: useId(), medium: useId(), hard: useId() };

  const { easyPercentage, mediumPercentage, hardPercentage } = difficultyPercentages({
    ...value,
    enabled: true,
  });
  const numbers = { easy: easyPercentage ?? 0, medium: mediumPercentage ?? 0, hard: hardPercentage ?? 0 };
  // Shown live while typing, not only after an attempt to save.
  const problem =
    checkDifficultyDistribution({ easyPercentage, mediumPercentage, hardPercentage }) ?? error;
  const total = DIFFICULTIES.reduce(
    (sum, difficulty) => sum + (Number.isFinite(numbers[difficulty]) ? numbers[difficulty] : 0),
    0,
  );

  function setPercentage(difficulty: Difficulty, text: string) {
    onChange({ ...value, percentages: { ...value.percentages, [difficulty]: text } });
  }

  return (
    <FieldSet data-invalid={error ? true : undefined}>
      <FieldLegend variant="label">Difficulty distribution</FieldLegend>
      <FieldDescription>
        Optionally specify how much of the assessment should be easy, medium, and hard.
      </FieldDescription>
      <Field orientation="horizontal">
        <Checkbox
          id={checkboxId}
          checked={value.enabled}
          onCheckedChange={(enabled) => onChange({ ...value, enabled })}
        />
        <FieldLabel htmlFor={checkboxId} className="font-normal">
          Specify difficulty distribution
        </FieldLabel>
      </Field>

      {value.enabled && (
        <div className="flex max-w-md flex-col gap-3">
          <div className="grid grid-cols-3 gap-3">
            {DIFFICULTIES.map((difficulty) => (
              <div key={difficulty} className="flex min-w-0 flex-col gap-2">
                <Label htmlFor={inputIds[difficulty]} className="gap-1.5">
                  <DifficultySwatch difficulty={difficulty} />
                  <span>
                    {DIFFICULTY_LABELS[difficulty]}
                    <span className="sr-only"> percentage</span>
                  </span>
                </Label>
                <div className="flex items-center gap-1.5">
                  <Input
                    id={inputIds[difficulty]}
                    type="number"
                    inputMode="numeric"
                    min={0}
                    max={100}
                    step={1}
                    className="tabular-nums"
                    value={value.percentages[difficulty]}
                    onChange={(event) => setPercentage(difficulty, event.target.value)}
                    aria-invalid={error ? true : undefined}
                    aria-describedby={totalId}
                  />
                  <span aria-hidden className="text-sm text-muted-foreground">
                    %
                  </span>
                </div>
              </div>
            ))}
          </div>

          <DifficultyBar percentages={numbers} className="mt-1" />

          <p
            id={totalId}
            aria-live="polite"
            className={cn(
              "flex items-start gap-1.5 text-sm",
              problem ? "text-destructive" : "text-muted-foreground",
            )}
          >
            {problem ? (
              <CircleAlert className="mt-0.5 size-4 shrink-0" />
            ) : (
              <CircleCheck className="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
            )}
            <span>
              Total <span className="font-medium tabular-nums">{total}%</span>
              {problem && <>. {problem}</>}
            </span>
          </p>
        </div>
      )}
    </FieldSet>
  );
}
