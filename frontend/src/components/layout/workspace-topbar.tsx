"use client";

import { Bell, ChevronDown, Menu, Search } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { LogoutButton } from "@/components/auth/logout-button";
import { WorkspaceSidebar } from "@/components/layout/workspace-sidebar";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import type { Professor } from "@/lib/auth/current-professor";

export function WorkspaceTopbar({ professor }: { professor: Professor }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const name = professor.fullName || professor.email;
  const initials = name
    .split(/\s+/)
    .map((word) => word[0])
    .filter(Boolean);

  return (
    <header className="workspace-topbar">
      <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
        <SheetTrigger
          render={
            <Button
              variant="ghost"
              size="icon"
              className="lg:hidden"
              aria-label="Open menu"
            />
          }
        >
          <Menu />
        </SheetTrigger>
        <SheetContent
          side="left"
          className="workspace-theme w-72 gap-0 p-0"
          showCloseButton={false}
        >
          <SheetTitle className="sr-only">Workspace menu</SheetTitle>
          <WorkspaceSidebar
            professor={professor}
            onNavigate={() => setMenuOpen(false)}
          />
        </SheetContent>
      </Sheet>
      <div
        className="workspace-search"
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget))
            setSearchOpen(false);
        }}
      >
        <form action="/workspace/assessments" role="search">
          <Search className="size-4" aria-hidden="true" />
          <input
            name="search"
            type="search"
            aria-label="Search courses and assessments"
            placeholder="Search your courses or assessments…"
            value={query}
            onFocus={() => setSearchOpen(true)}
            onChange={(event) => {
              setQuery(event.target.value);
              setSearchOpen(true);
            }}
          />
        </form>
        {searchOpen && query.trim() && (
          <div className="workspace-search-results">
            <p>Search your workspace</p>
            <Link
              href={`/workspace/assessments?search=${encodeURIComponent(query)}`}
              onClick={() => setSearchOpen(false)}
            >
              Assessments matching “{query}”
            </Link>
            <Link
              href={`/workspace/courses?search=${encodeURIComponent(query)}`}
              onClick={() => setSearchOpen(false)}
            >
              Courses matching “{query}”
            </Link>
          </div>
        )}
      </div>
      <div className="workspace-topbar-actions">
        <Dialog>
          <DialogTrigger
            render={
              <Button variant="ghost" size="icon" aria-label="Notifications" />
            }
          >
            <Bell className="size-4.5" />
          </DialogTrigger>
          <DialogContent className="workspace-theme">
            <DialogHeader>
              <DialogTitle>Notifications</DialogTitle>
              <DialogDescription>
                You&apos;re all caught up. There are no notifications to show.
              </DialogDescription>
            </DialogHeader>
          </DialogContent>
        </Dialog>
        <span className="workspace-topbar-divider" aria-hidden="true" />
        <Dialog>
          <DialogTrigger
            render={
              <button
                type="button"
                className="workspace-profile"
                aria-label="Open profile and preferences"
              />
            }
          >
            <span className="workspace-avatar" aria-hidden="true">
              {initials.length > 1
                ? `${initials[0]}${initials[initials.length - 1]}`
                : initials[0]}
            </span>
            <span className="workspace-profile-name">
              <strong title={name}>{name}</strong>
              <span>
                Professor
                {professor.institution
                  ? ` · ${professor.institution}`
                  : " · ProfPilot AI"}
              </span>
            </span>
            <ChevronDown className="size-4" />
          </DialogTrigger>
          <DialogContent className="workspace-theme">
            <DialogHeader>
              <DialogTitle>Profile & preferences</DialogTitle>
              <DialogDescription>
                Your professor account and workspace appearance.
              </DialogDescription>
            </DialogHeader>
            <dl className="grid gap-3 text-sm">
              <div>
                <dt className="text-muted-foreground">Name</dt>
                <dd className="break-words">{name}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Email</dt>
                <dd className="break-words">{professor.email}</dd>
              </div>
              {professor.institution && (
                <div>
                  <dt className="text-muted-foreground">Institution</dt>
                  <dd>{professor.institution}</dd>
                </div>
              )}
            </dl>
            <div className="flex items-center justify-between border-t pt-3">
              <LogoutButton />
              <ThemeToggle />
            </div>
            <a
              href="/images/credits.txt"
              target="_blank"
              rel="noreferrer"
              className="text-xs text-muted-foreground underline underline-offset-4"
            >
              Image credits
            </a>
          </DialogContent>
        </Dialog>
      </div>
    </header>
  );
}
