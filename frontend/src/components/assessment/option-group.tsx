"use client";

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

export type Option<Value extends string> = { value: Value; label: string };

type OptionGroupProps<Value extends string> = {
  /** id of the element that names the group, such as a legend. */
  labelledBy: string;
  options: readonly Option<Value>[];
  value: Value;
  onChange: (value: Value) => void;
};

/** A row of buttons where exactly one option is chosen, such as 60, 90 or 120 minutes. */
export function OptionGroup<Value extends string>({
  labelledBy,
  options,
  value,
  onChange,
}: OptionGroupProps<Value>) {
  return (
    <ToggleGroup
      aria-labelledby={labelledBy}
      variant="outline"
      className="flex-wrap"
      value={[value]}
      onValueChange={(values) => {
        // Clicking the chosen option again would leave nothing chosen; keep it instead.
        if (values.length > 0) onChange(values[0] as Value);
      }}
    >
      {options.map((option) => (
        <ToggleGroupItem
          key={option.value}
          value={option.value}
          className="px-3 aria-pressed:border-primary aria-pressed:bg-primary aria-pressed:text-primary-foreground aria-pressed:hover:bg-primary/90 aria-pressed:hover:text-primary-foreground"
        >
          {option.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}
