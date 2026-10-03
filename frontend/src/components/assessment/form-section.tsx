import type { ReactNode } from "react";

type FormSectionProps = {
  title: string;
  /** id for the title, so a control can be named by it with aria-labelledby. */
  titleId?: string;
  description?: string;
  /** id for the description, so a field can point to it with aria-describedby. */
  descriptionId?: string;
  /** When the section holds a single field, its title becomes that field's label. */
  labelFor?: string;
  children: ReactNode;
};

/** A titled part of the Create assessment form. */
export function FormSection({
  title,
  titleId,
  description,
  descriptionId,
  labelFor,
  children,
}: FormSectionProps) {
  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-col gap-1 border-b pb-3">
        <h2 id={titleId} className="text-base font-semibold tracking-tight">
          {labelFor ? <label htmlFor={labelFor}>{title}</label> : title}
        </h2>
        {description && (
          <p id={descriptionId} className="text-sm text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {children}
    </section>
  );
}
