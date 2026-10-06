"use client";

import { BookOpen, ClipboardCheck, House, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { LogoutButton } from "@/components/auth/logout-button";
import { LinkPendingIcon } from "@/components/link-pending-icon";
import { Logo } from "@/components/logo";
import { ThemeToggle } from "@/components/theme-toggle";
import type { Professor } from "@/lib/auth/current-professor";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/workspace", label: "Home", icon: House },
  { href: "/workspace/courses", label: "Courses", icon: BookOpen },
  { href: "/workspace/assessments", label: "Assessments", icon: ClipboardCheck },
  { href: "/workspace/preferences", label: "Preferences", icon: SlidersHorizontal },
];

type WorkspaceSidebarProps = {
  professor: Professor;
  /** Called when a link is clicked, e.g. to close the mobile menu. */
  onNavigate?: () => void;
};

/** Workspace navigation, plus the signed-in professor's details and logout. */
export function WorkspaceSidebar({ professor, onNavigate }: WorkspaceSidebarProps) {
  const pathname = usePathname();

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-16 shrink-0 items-center px-5">
        <Link href="/workspace" onClick={onNavigate}>
          <Logo />
        </Link>
      </div>

      <nav aria-label="Workspace" className="flex flex-1 flex-col gap-1 px-3 py-4">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const isActive = href === "/workspace" ? pathname === href : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              onClick={onNavigate}
              aria-current={isActive ? "page" : undefined}
              className={cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                isActive && "bg-sidebar-accent text-sidebar-accent-foreground",
              )}
            >
              <LinkPendingIcon>
                <Icon className="size-4" />
              </LinkPendingIcon>
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="border-t p-3">
        <div className="flex items-center gap-3 p-2">
          <span
            aria-hidden
            className="flex size-9 shrink-0 items-center justify-center rounded-full bg-muted text-sm font-medium"
          >
            {getInitials(professor)}
          </span>
          {/* `title` shows the full text on hover when it is cut off. */}
          <div className="min-w-0 text-sm">
            <p className="truncate font-medium" title={professor.fullName ?? professor.email}>
              {professor.fullName ?? professor.email}
            </p>
            {professor.fullName && (
              <p className="truncate text-muted-foreground" title={professor.email}>
                {professor.email}
              </p>
            )}
            {professor.institution && (
              <p className="truncate text-muted-foreground" title={professor.institution}>
                {professor.institution}
              </p>
            )}
          </div>
        </div>
        <div className="mt-1 flex items-center justify-between">
          <LogoutButton />
          <ThemeToggle />
        </div>
      </div>
    </div>
  );
}

function getInitials({ fullName, email }: Professor) {
  if (!fullName) return email.charAt(0).toUpperCase();
  const words = fullName.split(/\s+/);
  const last = words.length > 1 ? words[words.length - 1] : "";
  return (words[0].charAt(0) + last.charAt(0)).toUpperCase();
}
