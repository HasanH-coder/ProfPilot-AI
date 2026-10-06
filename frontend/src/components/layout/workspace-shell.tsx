import type { ReactNode } from "react";

import { WorkspaceSidebar } from "@/components/layout/workspace-sidebar";
import { WorkspaceTopbar } from "@/components/layout/workspace-topbar";
import type { Professor } from "@/lib/auth/current-professor";

/** Shared presentation only. The workspace layout remains responsible for authentication. */
export function WorkspaceShell({
  professor,
  children,
}: {
  professor: Professor;
  children: ReactNode;
}) {
  return (
    <div className="workspace-theme workspace-shell">
      <a href="#workspace-content" className="workspace-skip-link">
        Skip to content
      </a>
      <aside aria-label="Workspace navigation" className="workspace-desktop-sidebar">
        <WorkspaceSidebar professor={professor} />
      </aside>
      <div className="workspace-body">
        <WorkspaceTopbar professor={professor} />
        <main id="workspace-content" tabIndex={-1} className="workspace-main">
          <div aria-hidden="true" className="workspace-campus-art" />
          <div className="workspace-content">{children}</div>
        </main>
      </div>
    </div>
  );
}
