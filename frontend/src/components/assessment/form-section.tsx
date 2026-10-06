import {
  BookOpen,
  Database,
  FileText,
  MessageSquareText,
  Paperclip,
  Settings2,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";

const SECTION_ICONS: Record<string, LucideIcon> = {
  "Course & Assessment Details": BookOpen,
  "Course material": FileText,
  "Previous assessments": Database,
  "Exam design": Settings2,
  "Additional notes": MessageSquareText,
  "Additional images or attachments": Paperclip,
  "Tell ProfPilot what you want": Sparkles,
};

type FormSectionProps = {
  title: string;
  titleId?: string;
  description?: string;
  descriptionId?: string;
  labelFor?: string;
  children: ReactNode;
};

export function FormSection({
  title,
  titleId,
  description,
  descriptionId,
  labelFor,
  children,
}: FormSectionProps) {
  const Icon = SECTION_ICONS[title] ?? FileText;
  return (
    <section className="assessment-section">
      <div className="assessment-section-header">
        <h2 id={titleId} className="assessment-section-title">
          <Icon className="size-5 shrink-0" aria-hidden="true" />
          {labelFor ? <label htmlFor={labelFor}>{title}</label> : title}
        </h2>
        {description && (
          <p
            id={descriptionId}
            className="text-xs leading-relaxed text-muted-foreground"
          >
            {description}
          </p>
        )}
      </div>
      {children}
    </section>
  );
}
