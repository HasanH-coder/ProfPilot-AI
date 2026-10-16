"use client";

import { ChevronDown, CircleAlert, CircleCheck, Lightbulb, RotateCcw } from "lucide-react";
import { useId, useState } from "react";

import { BloomChart, BloomSwatch } from "@/components/assessment/bloom-chart";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FieldDescription, FieldLegend, FieldSet } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import {
  BLOOM_LABELS,
  BLOOM_LEVELS,
  checkBloomDistribution,
  chosenBloomDistribution,
  CUSTOM_BLOOM_EXAMPLE,
  type AssessmentDraft,
  type BloomDistribution,
  type BloomLevel,
  type LearningFocus as Focus,
} from "@/lib/assessments/draft";
import { cn } from "@/lib/utils";

export type LearningFocusValue = {
  focus: Focus;
  /** What is typed in each custom box, kept while another focus is chosen, so Custom can restore it. */
  custom: Record<BloomLevel, string>;
};

type LearningFocusDraft = Pick<AssessmentDraft, "learningFocus" | "bloomDistribution">;

const asText = (distribution: BloomDistribution) =>
  Object.fromEntries(BLOOM_LEVELS.map((level) => [level, `${distribution[level]}`])) as Record<BloomLevel, string>;

const asNumbers = (custom: Record<BloomLevel, string>) =>
  Object.fromEntries(
    BLOOM_LEVELS.map((level) => [level, custom[level].trim() ? Number(custom[level]) : Number.NaN]),
  ) as BloomDistribution;

/** A new assessment: Automatic, with the example split ready if the professor customizes it. */
export const LEARNING_FOCUS_DEFAULT: LearningFocusValue = { focus: "automatic", custom: asText(CUSTOM_BLOOM_EXAMPLE) };

/** The focus and percentages to save. Only a custom focus saves percentages. */
export function learningFocusDraft({ focus, custom }: LearningFocusValue): LearningFocusDraft {
  return { learningFocus: focus, bloomDistribution: focus === "custom" ? asNumbers(custom) : null };
}

/** The reverse, for a saved draft. */
export function learningFocusValue(draft: LearningFocusDraft): LearningFocusValue {
  return {
    focus: draft.learningFocus,
    custom: draft.bloomDistribution ? asText(draft.bloomDistribution) : LEARNING_FOCUS_DEFAULT.custom,
  };
}

const OPTIONS: { focus: Exclude<Focus, "custom">; title: string; description: string }[] = [
  {
    focus: "automatic",
    title: "Automatic",
    description: "ProfPilot suggests a suitable cognitive distribution based on your course materials and learning objectives.",
  },
  { focus: "balanced", title: "Balanced", description: "A balanced mix of knowledge, application, and reasoning." },
  {
    focus: "practical",
    title: "Practical & Problem-Solving",
    description: "Focus on applying concepts and solving practical problems.",
  },
  {
    focus: "critical",
    title: "Critical Thinking",
    description: "Emphasize analysis, evaluation, and justified decisions.",
  },
];

type LearningFocusProps = {
  value: LearningFocusValue;
  onChange: (value: LearningFocusValue) => void;
  /** ProfPilot's split from the latest plan, shown while the focus is Automatic. */
  recommendation?: BloomDistribution | null;
  /** Set after an attempt to save an invalid custom split. */
  error?: string;
};

/**
 * Exam Learning Focus: how the exam's marks are spread across Bloom's levels.
 * Four choices, and an advanced panel to set the six percentages directly.
 * Changing a percentage makes the split the professor's own (Custom); nothing
 * is adjusted automatically, and a split that doesn't total 100% can't be saved.
 */
export function LearningFocus({ value, onChange, recommendation = null, error }: LearningFocusProps) {
  const name = useId();
  const panelId = useId();
  const totalId = useId();
  const [expanded, setExpanded] = useState(false);
  // After a failed save, the invalid boxes must be visible (and focusable).
  const open = expanded || Boolean(error);

  const draft = learningFocusDraft(value);
  const shown = chosenBloomDistribution(draft) ?? recommendation;
  const problem = value.focus === "custom" ? (checkBloomDistribution("custom", draft.bloomDistribution) ?? error) : undefined;
  const total = shown ? BLOOM_LEVELS.reduce((sum, level) => sum + (Number.isFinite(shown[level]) ? shown[level] : 0), 0) : 0;

  /** Editing a share makes the split the professor's own, starting from what was shown. */
  function setShare(level: BloomLevel, text: string) {
    const start = value.focus === "custom" ? value.custom : asText(shown ?? CUSTOM_BLOOM_EXAMPLE);
    onChange({ focus: "custom", custom: { ...start, [level]: text } });
  }

  return (
    <FieldSet data-invalid={problem ? true : undefined}>
      <FieldLegend variant="label">Exam Learning Focus</FieldLegend>
      <FieldDescription>Choose how your exam assesses thinking skills using Bloom&apos;s Taxonomy.</FieldDescription>
      <div role="radiogroup" aria-label="Learning focus" className="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-4">
        {OPTIONS.map((option) => {
          const checked = value.focus === option.focus;
          return (
            <label
              key={option.focus}
              className={cn(
                "flex cursor-pointer gap-2.5 rounded-lg border bg-card p-3 transition-colors hover:border-primary/40 hover:bg-accent/40",
                "has-[:focus-visible]:ring-3 has-[:focus-visible]:ring-ring/50",
                checked && "border-primary bg-accent/60 hover:bg-accent/60",
              )}
            >
              <input
                type="radio"
                name={name}
                value={option.focus}
                checked={checked}
                onChange={() => onChange({ ...value, focus: option.focus })}
                className="mt-0.5 size-4 shrink-0 accent-primary"
              />
              <span className="flex min-w-0 flex-col gap-1">
                <span className="flex flex-wrap items-center gap-1.5 text-sm font-medium">
                  {option.title}
                  {option.focus === "automatic" && (
                    <Badge variant="outline" className="border-emerald-600/40 text-[10px] text-emerald-700 dark:text-emerald-400">
                      Recommended
                    </Badge>
                  )}
                </span>
                <span className="text-xs leading-relaxed text-muted-foreground">{option.description}</span>
              </span>
            </label>
          );
        })}
      </div>

      <div className="rounded-lg border">
        <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5">
          <button
            type="button"
            aria-expanded={open}
            aria-controls={panelId}
            onClick={() => setExpanded(!open)}
            className="flex min-w-0 items-start gap-2 rounded-md text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <ChevronDown className={cn("mt-0.5 size-4 shrink-0 transition-transform", !open && "-rotate-90")} aria-hidden />
            <span className="flex flex-col gap-0.5">
              <span className="flex flex-wrap items-center gap-1.5 text-sm font-medium">
                Advanced: Bloom&apos;s Taxonomy
                {value.focus === "custom" && <Badge variant="secondary">Custom</Badge>}
              </span>
              <span className="text-xs text-muted-foreground">Adjust the distribution across cognitive levels.</span>
            </span>
          </button>
          {value.focus !== "automatic" && (
            <Button type="button" variant="outline" size="sm" onClick={() => onChange({ ...value, focus: "automatic" })}>
              <RotateCcw />
              Reset to Recommended
            </Button>
          )}
        </div>

        {open && (
          <div id={panelId} className="flex flex-col gap-4 border-t p-3">
            {shown ? (
              <div className="grid gap-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
                <div className="flex flex-col gap-2.5">
                  {BLOOM_LEVELS.map((level) => (
                    <ShareControl
                      key={level}
                      level={level}
                      text={value.focus === "custom" ? value.custom[level] : `${shown[level]}`}
                      invalid={Boolean(problem)}
                      describedBy={totalId}
                      onChange={(text) => setShare(level, text)}
                    />
                  ))}
                  <p
                    id={totalId}
                    aria-live="polite"
                    className={cn("flex items-start gap-1.5 border-t pt-2.5 text-sm", problem ? "text-destructive" : "text-muted-foreground")}
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
                <div className="flex flex-col gap-2 rounded-lg border p-3">
                  <p className="text-sm font-medium">Cognitive distribution summary</p>
                  <BloomChart distribution={shown} />
                  <p className="text-xs text-muted-foreground">{SOURCE_NOTES[value.focus]}</p>
                </div>
              </div>
            ) : (
              <div className="flex flex-col items-start gap-3 rounded-lg bg-muted/50 p-3 text-sm">
                <p className="text-muted-foreground">
                  ProfPilot will recommend a distribution from your course material, objectives and question formats when
                  it prepares your plan. You&apos;ll review it before anything is generated.
                </p>
                <Button type="button" variant="outline" size="sm" onClick={() => onChange({ ...value, focus: "custom" })}>
                  Set the percentages yourself
                </Button>
              </div>
            )}
            {value.focus !== "custom" && shown && (
              <button
                type="button"
                className="w-fit text-xs text-primary underline underline-offset-4"
                onClick={() => onChange({ ...value, focus: "custom" })}
              >
                Use my custom percentages instead
              </button>
            )}
            <p className="flex items-start gap-2 rounded-lg bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
              <Lightbulb className="mt-0.5 size-3.5 shrink-0 text-amber-500" aria-hidden />
              <span>
                Shares are of the exam&apos;s marks, not its number of questions. Bloom&apos;s levels are separate from
                difficulty: an Analyze question can be medium and an Apply question hard. ProfPilot respects both.
              </span>
            </p>
          </div>
        )}
      </div>
    </FieldSet>
  );
}

const SOURCE_NOTES: Record<Focus, string> = {
  automatic: "Recommended by ProfPilot in your latest plan. It is updated when the plan is prepared again.",
  balanced: "The Balanced preset: a suggested starting point. Change any share to make it your own.",
  practical: "The Practical & Problem-Solving preset: a suggested starting point. Change any share to make it your own.",
  critical: "The Critical Thinking preset: a suggested starting point. Change any share to make it your own.",
  custom: "Your own distribution. ProfPilot follows it exactly and tells you if it can't be met.",
};

function ShareControl({
  level,
  text,
  invalid,
  describedBy,
  onChange,
}: {
  level: BloomLevel;
  text: string;
  invalid: boolean;
  describedBy: string;
  onChange: (text: string) => void;
}) {
  const labelId = useId();
  const inputId = useId();
  const number = Number(text);
  return (
    <div className="grid grid-cols-[6.5rem_minmax(0,1fr)_4.5rem] items-center gap-3">
      <label id={labelId} htmlFor={inputId} className="flex items-center gap-2 text-sm">
        <BloomSwatch level={level} />
        {BLOOM_LABELS[level]}
      </label>
      <Slider
        aria-labelledby={labelId}
        min={0}
        max={100}
        step={1}
        format={{ style: "unit", unit: "percent" }}
        value={[Number.isFinite(number) ? Math.min(100, Math.max(0, number)) : 0]}
        onValueChange={(next) => onChange(`${Array.isArray(next) ? next[0] : next}`)}
      />
      <div className="flex items-center gap-1">
        <Input
          id={inputId}
          type="number"
          inputMode="numeric"
          min={0}
          max={100}
          step={1}
          className="tabular-nums"
          value={text}
          onChange={(event) => onChange(event.target.value)}
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
        />
        <span aria-hidden className="text-sm text-muted-foreground">
          %
        </span>
      </div>
    </div>
  );
}
