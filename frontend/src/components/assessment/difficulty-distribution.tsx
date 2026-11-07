"use client";

import { CircleAlert, CircleCheck } from "lucide-react";
import { useId } from "react";

import { DifficultyBar, DifficultySwatch } from "@/components/assessment/difficulty-bar";
import { Checkbox } from "@/components/ui/checkbox";
import { Field, FieldDescription, FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  checkDifficultyDefinitions,
  checkDifficultyDistribution,
  DEFAULT_DIFFICULTY_DEFINITIONS,
  DEFAULT_DIFFICULTY_DISTRIBUTION,
  DIFFICULTIES,
  DIFFICULTY_DEFINITION_LIMIT,
  DIFFICULTY_LABELS,
  savedDifficultyDefinitions,
  type Difficulty,
  type DifficultyDefinitions,
  type DifficultyPercentages,
} from "@/lib/assessments/draft";
import { cn } from "@/lib/utils";

export type DifficultyDistributionValue = {
  enabled: boolean;
  /** What is typed in each box, kept while the distribution is switched off, so switching it back on restores it. */
  percentages: Record<Difficulty, string>;
  /** What is in each definition box: the defaults until the professor rewrites one. Independent of the split. */
  definitions: DifficultyDefinitions;
};

/** Switched off. Switching it on starts the boxes at 30 / 40 / 30. */
export const DIFFICULTY_NOT_SPECIFIED: DifficultyDistributionValue = {
  enabled: false,
  percentages: {
    easy: `${DEFAULT_DIFFICULTY_DISTRIBUTION.easy}`,
    medium: `${DEFAULT_DIFFICULTY_DISTRIBUTION.medium}`,
    hard: `${DEFAULT_DIFFICULTY_DISTRIBUTION.hard}`,
  },
  definitions: DEFAULT_DIFFICULTY_DEFINITIONS,
};

/** The definitions to save: null while every box is still the default (or empty). */
export function difficultyDefinitionsDraft({ definitions }: DifficultyDistributionValue): DifficultyDefinitions | null {
  return savedDifficultyDefinitions(definitions);
}

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
export function difficultyDistributionValue(
  draft: DifficultyPercentages & { difficultyDefinitions: DifficultyDefinitions | null },
): DifficultyDistributionValue {
  const { easyPercentage: easy, mediumPercentage: medium, hardPercentage: hard } = draft;
  const definitions = draft.difficultyDefinitions ?? DEFAULT_DIFFICULTY_DEFINITIONS;
  if (easy === null || medium === null || hard === null) return { ...DIFFICULTY_NOT_SPECIFIED, definitions };
  return { enabled: true, percentages: { easy: `${easy}`, medium: `${medium}`, hard: `${hard}` }, definitions };
}

type DifficultyDistributionProps = {
  value: DifficultyDistributionValue;
  onChange: (value: DifficultyDistributionValue) => void;
  /** Set after an attempt to save an invalid distribution. */
  error?: string;
  /** Set after an attempt to save invalid definitions. */
  definitionsError?: string;
};

/**
 * What easy, medium and hard mean for this assessment: ProfPilot's defaults, which the professor can rewrite.
 * The boxes start with the defaults, so what ProfPilot will follow is always visible.
 */
function DifficultyDefinitionsEditor({
  value,
  onChange,
  error,
}: {
  value: DifficultyDistributionValue;
  onChange: (value: DifficultyDistributionValue) => void;
  error?: string;
}) {
  const errorId = useId();
  const ids: Record<Difficulty, string> = { easy: useId(), medium: useId(), hard: useId() };
  const saved = savedDifficultyDefinitions(value.definitions);
  const customised = saved !== null;
  const problem = checkDifficultyDefinitions(saved) ?? error;

  return (
    <details className="group max-w-2xl rounded-lg border bg-card" open={problem ? true : undefined}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2 text-sm font-medium">
        <span>What do easy, medium and hard mean?</span>
        <span className="text-xs font-normal text-muted-foreground">
          {customised ? "Your definitions" : "ProfPilot's defaults"}
        </span>
      </summary>
      <div className="flex flex-col gap-3 border-t px-3 py-3">
        <p className="text-sm text-muted-foreground">
          ProfPilot writes and checks every question against these. Edit any of them to use your own meaning. The
          percentages describe the share of prepared students who should answer correctly, and are an aim, not a
          measurement.
        </p>
        {DIFFICULTIES.map((difficulty) => (
          <div key={difficulty} className="flex flex-col gap-1.5">
            <Label htmlFor={ids[difficulty]} className="gap-1.5">
              <DifficultySwatch difficulty={difficulty} />
              {DIFFICULTY_LABELS[difficulty]}
            </Label>
            <Textarea
              id={ids[difficulty]}
              rows={3}
              maxLength={DIFFICULTY_DEFINITION_LIMIT * 2}
              value={value.definitions[difficulty]}
              aria-invalid={problem ? true : undefined}
              aria-describedby={problem ? errorId : undefined}
              onChange={(event) =>
                onChange({ ...value, definitions: { ...value.definitions, [difficulty]: event.target.value } })
              }
            />
          </div>
        ))}
        {problem && (
          <p id={errorId} role="alert" className="flex items-start gap-1.5 text-sm text-destructive">
            <CircleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{problem}</span>
          </p>
        )}
        <div>
          <button
            type="button"
            className="text-sm underline underline-offset-4 disabled:no-underline disabled:opacity-50"
            disabled={!customised}
            onClick={() => onChange({ ...value, definitions: DEFAULT_DIFFICULTY_DEFINITIONS })}
          >
            Use ProfPilot&apos;s defaults
          </button>
        </div>
      </div>
    </details>
  );
}

/**
 * An optional split of the assessment into easy, medium and hard, typed as
 * three percentages. Nothing is adjusted automatically: while they don't add
 * up to 100%, a message under the boxes says so, and the draft can't be saved.
 */
export function DifficultyDistribution({ value, onChange, error, definitionsError }: DifficultyDistributionProps) {
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

      <DifficultyDefinitionsEditor value={value} onChange={onChange} error={definitionsError} />
    </FieldSet>
  );
}
