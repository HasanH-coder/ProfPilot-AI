import type { ComponentProps } from "react";

import {
  Field,
  FieldDescription,
  FieldError,
  FieldLabel,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";

type FormFieldProps = ComponentProps<typeof Input> & {
  id: string;
  label: string;
  description?: string;
  error?: string;
};

/** A labelled input with an optional hint and error message, linked for screen readers. */
export function FormField({ id, label, description, error, ...inputProps }: FormFieldProps) {
  const hintId = error ? `${id}-error` : description ? `${id}-description` : undefined;

  return (
    <Field data-invalid={error ? true : undefined}>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={hintId}
        {...inputProps}
      />
      {error ? (
        <FieldError id={hintId}>{error}</FieldError>
      ) : (
        description && <FieldDescription id={hintId}>{description}</FieldDescription>
      )}
    </Field>
  );
}
