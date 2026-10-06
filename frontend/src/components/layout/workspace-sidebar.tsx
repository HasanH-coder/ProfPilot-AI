"use client";

import {
  BookOpen,
  ClipboardCheck,
  FileText,
  House,
  Settings,
  Sparkles,
} from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { LogoutButton } from "@/components/auth/logout-button";
import { LinkPendingIcon } from "@/components/link-pending-icon";
import { ThemeToggle } from "@/components/theme-toggle";
import type { Professor } from "@/lib/auth/current-professor";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/workspace", label: "Home", icon: House },
  { href: "/workspace/courses", label: "Courses", icon: BookOpen },
  {
    href: "/workspace/assessments",
    label: "Assessments",
    icon: ClipboardCheck,
  },
];

type WorkspaceSidebarProps = {
  professor: Professor;
  onNavigate?: () => void;
};

export function WorkspaceSidebar({ onNavigate }: WorkspaceSidebarProps) {
  const pathname = usePathname();

  return (
    <div className="workspace-sidebar">
      <div className="workspace-brand">
        <Link
          href="/workspace"
          onClick={onNavigate}
          aria-label="American University of Beirut — ProfPilot home"
        >
          <Image
            src="/images/aub-logo.png"
            alt="American University of Beirut"
            width={300}
            height={100}
            className="workspace-brand-logo"
          />
        </Link>
      </div>
      <nav aria-label="Workspace" className="workspace-nav">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active =
            href === "/workspace"
              ? pathname === href
              : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={cn("workspace-nav-item", active && "is-active")}
            >
              <LinkPendingIcon>
                <Icon className="size-5" />
              </LinkPendingIcon>
              {label}
            </Link>
          );
        })}
        <Link
          href="/workspace/assessments/new?assistant=setup"
          onClick={onNavigate}
          className="workspace-nav-item"
        >
          <Sparkles className="size-5" />
          AI Assistant
        </Link>
        {[{ label: "Resources", icon: FileText }].map(
          ({ label, icon: Icon }) => (
            <span
              key={label}
              aria-disabled="true"
              className="workspace-nav-item workspace-nav-upcoming"
              title={`${label} is coming soon`}
            >
              <Icon className="size-5" aria-hidden="true" />
              <span>{label}</span>
              <span className="workspace-soon">Soon</span>
            </span>
          ),
        )}
        <Link
          href="/workspace/preferences"
          onClick={onNavigate}
          aria-current={
            pathname === "/workspace/preferences" ? "page" : undefined
          }
          className={cn(
            "workspace-nav-item",
            pathname === "/workspace/preferences" && "is-active",
          )}
        >
          <Settings className="size-5" />
          Settings
        </Link>
      </nav>
      <div aria-hidden="true" className="workspace-sidebar-art" />
      <div className="workspace-sidebar-footer">
        <p>
          ProfPilot <span>AI</span>
        </p>
        <div className="flex items-center justify-between">
          <LogoutButton />
          <ThemeToggle />
        </div>
      </div>
    </div>
  );
}
