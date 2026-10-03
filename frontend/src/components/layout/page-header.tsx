import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

type PageHeaderProps = {
  title: string;
  description: ReactNode;
  /** A link to the page this one belongs to, shown above the title. */
  back?: { href: string; label: string };
  /** Shown next to the title, such as a status badge. */
  badge?: ReactNode;
  /** Optional actions, such as buttons, shown below the description. */
  children?: ReactNode;
};

/** Title and description at the top of a workspace page. */
export function PageHeader({ title, description, back, badge, children }: PageHeaderProps) {
  return (
    <div className="flex flex-col gap-2">
      {back && (
        <Link
          href={back.href}
          className="mb-2 inline-flex w-fit max-w-full items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="size-4 shrink-0" />
          <span className="truncate">{back.label}</span>
        </Link>
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h1 className="min-w-0 text-2xl font-semibold tracking-tight break-words sm:text-3xl">
          {title}
        </h1>
        {badge}
      </div>
      <p className="max-w-2xl text-muted-foreground">{description}</p>
      {children && <div className="mt-4 flex flex-wrap gap-2">{children}</div>}
    </div>
  );
}
