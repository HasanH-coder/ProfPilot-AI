import type { ReactNode } from "react";

type PageHeaderProps = {
  title: string;
  description: string;
  /** Optional actions, such as buttons, shown below the description. */
  children?: ReactNode;
};

/** Title and description at the top of a workspace page. */
export function PageHeader({ title, description, children }: PageHeaderProps) {
  return (
    <div className="flex flex-col gap-2">
      <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{title}</h1>
      <p className="max-w-2xl text-muted-foreground">{description}</p>
      {children && <div className="mt-4 flex flex-wrap gap-2">{children}</div>}
    </div>
  );
}
