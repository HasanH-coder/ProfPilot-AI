"use client";

import { Menu } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { WorkspaceSidebar } from "@/components/layout/workspace-sidebar";
import { Logo } from "@/components/logo";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import type { Professor } from "@/lib/auth/current-professor";

/** Top bar for phones and tablets. The menu button opens the sidebar in a sliding panel. */
export function WorkspaceMobileHeader({ professor }: { professor: Professor }) {
  const [isMenuOpen, setIsMenuOpen] = useState(false);

  return (
    <header className="sticky top-0 z-40 flex h-14 items-center gap-2 border-b bg-background/80 px-3 backdrop-blur lg:hidden">
      <Sheet open={isMenuOpen} onOpenChange={setIsMenuOpen}>
        <SheetTrigger render={<Button variant="ghost" size="icon" aria-label="Open menu" />}>
          <Menu />
        </SheetTrigger>
        <SheetContent side="left" className="w-72 gap-0 p-0">
          <SheetTitle className="sr-only">Workspace menu</SheetTitle>
          <WorkspaceSidebar professor={professor} onNavigate={() => setIsMenuOpen(false)} />
        </SheetContent>
      </Sheet>
      <Link href="/workspace">
        <Logo />
      </Link>
      <div className="ml-auto">
        <ThemeToggle />
      </div>
    </header>
  );
}
