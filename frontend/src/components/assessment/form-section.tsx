import {
  BookOpen,
  ChevronDown,
  CircleAlert,
  Database,
  FileText,
  MessageSquareText,
  Paperclip,
  Settings2,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import { useId, type ReactNode } from "react";

import { cn } from "@/lib/utils";

const SECTION_ICONS: Record<string, LucideIcon> = {
  "Course & Assessment Details": BookOpen,
  "Course material": FileText,
  "Previous assessments": Database,
  "Exam design": Settings2,
  "Additional notes": MessageSquareText,
  "Additional images or attachments": Paperclip,
  "Tell ProfPilot what you want": Sparkles,
};

/** Whether a section is open, and what it shows while it is closed. Owned by the form. */
export type SectionState = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** A short description of what the section holds, shown while it is closed. */
  summary: string;
  /** The section has a field the professor must fix. */
  hasError?: boolean;
};

type FormSectionProps = SectionState & {
  title: string;
  titleId?: string;
  description?: string;
  descriptionId?: string;
  /** The field the title names (a textarea), which otherwise has no visible label. */
  labelFor?: string;
  children: ReactNode;
};

/**
 * A card of the Create assessment form that the professor can close and open
 * again. Closed, its content is only hidden, never unmounted, so nothing typed,
 * uploaded or running in it is lost.
 */
export function FormSection({
  title,
  titleId: givenTitleId,
  description,
  descriptionId,
  labelFor,
  open,
  onOpenChange,
  summary,
  hasError = false,
  children,
}: FormSectionProps) {
  const ownTitleId = useId();
  const titleId = givenTitleId ?? ownTitleId;
  const panelId = useId();
  const Icon = SECTION_ICONS[title] ?? FileText;
  return (
    <section className="assessment-section" aria-labelledby={titleId}>
      <div className="assessment-section-header">
        <h2 className="assessment-section-title">
          {/* type="button": toggling a section never submits the form. */}
          <button
            type="button"
            aria-expanded={open}
            aria-controls={panelId}
            onClick={() => onOpenChange(!open)}
            className="flex w-full min-w-0 items-center gap-2.5 rounded-md text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <Icon className="size-5 shrink-0" aria-hidden="true" />
            <span id={titleId}>{title}</span>
            {!open && (
              <span className="min-w-0 truncate font-sans text-xs font-normal text-muted-foreground">
                <span aria-hidden="true">— </span>
                {summary}
              </span>
            )}
            {hasError && (
              <span className="flex shrink-0 items-center gap-1 font-sans text-xs font-medium text-destructive">
                <CircleAlert className="size-3.5 text-destructive!" aria-hidden="true" />
                Needs attention
              </span>
            )}
            <ChevronDown
              className={cn(
                "ml-auto size-4 shrink-0 text-muted-foreground! motion-safe:transition-transform",
                open && "rotate-180",
              )}
              aria-hidden="true"
            />
          </button>
        </h2>
        {description && (
          <p id={descriptionId} hidden={!open} className="text-xs leading-relaxed text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {/* Tailwind's preflight keeps [hidden] hidden over the flex layout. */}
      <div id={panelId} hidden={!open} className="flex flex-col gap-3">
        {labelFor && (
          <label htmlFor={labelFor} className="sr-only">
            {title}
          </label>
        )}
        {children}
      </div>
    </section>
  );
}
