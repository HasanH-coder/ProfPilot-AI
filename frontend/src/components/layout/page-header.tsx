import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { LinkPendingIcon } from "@/components/link-pending-icon";

type PageHeaderProps = {
  title: string;
  description: ReactNode;
  back?: { href: string; label: string };
  badge?: ReactNode;
  highlight?: string;
  children?: ReactNode;
};

export function PageHeader({
  title,
  description,
  back,
  badge,
  highlight,
  children,
}: PageHeaderProps) {
  return (
    <div className="workspace-page-header flex flex-col gap-2">
      {back && (
        <Link
          href={back.href}
          className="workspace-back-link mb-1 inline-flex w-fit max-w-full items-center gap-1.5 text-xs font-medium transition-colors hover:underline"
        >
          <LinkPendingIcon>
            <ArrowLeft className="size-3.5 shrink-0" />
          </LinkPendingIcon>
          <span className="truncate">{back.label}</span>
        </Link>
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h1 className="workspace-page-title min-w-0 font-semibold tracking-tight break-words">
          {highlight && title.endsWith(highlight) ? (
            <>
              {title.slice(0, -highlight.length)}
              <span>{highlight}</span>
            </>
          ) : (
            title
          )}
        </h1>
        {badge}
      </div>
      <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
        {description}
      </p>
      {children && <div className="mt-4 flex flex-wrap gap-2">{children}</div>}
    </div>
  );
}
