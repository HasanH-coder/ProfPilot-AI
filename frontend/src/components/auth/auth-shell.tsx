import type { ReactNode } from "react";
import { ArrowRight, ClipboardCheck, Headphones } from "lucide-react";
import Link from "next/link";

import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";
import { buttonVariants } from "@/components/ui/button";

/** A shared AUB entry layout with the same palette and display type as the workspace. */
export function AuthShell({ children, mode = "login" }: { children: ReactNode; mode?: "login" | "signup" }) {
  return (
    <div className="public-theme public-shell">
      <SiteHeader>
        <Link href={mode === "login" ? "/signup" : "/login"} className={buttonVariants({ variant: "outline" })}>
          {mode === "login" ? "Sign up" : "Log in"}
        </Link>
      </SiteHeader>
      <main className="auth-main">
        <section className="auth-story" aria-labelledby="auth-story-title">
          <div className="auth-story-art" aria-hidden="true" />
          <div className="relative z-10">
            <p className="public-eyebrow">THE PROFESSOR WORKSPACE</p>
            <h2 id="auth-story-title" className="public-display auth-story-title">
              Your expertise.<br /><span>More room to teach.</span>
            </h2>
            <p className="auth-story-description">
              Turn your course materials into thoughtful assessments, with an AI assistant that works alongside you.
            </p>
            <div className="auth-story-features">
              <div><span className="public-icon"><ClipboardCheck aria-hidden="true" /></span><p><strong>From material to assessment</strong><span>Plan, generate, review, and download your exam.</span></p></div>
              <div><span className="public-icon"><Headphones aria-hidden="true" /></span><p><strong>Work your way</strong><span>Generate a full exam or build it together by voice.</span></p></div>
            </div>
          </div>
          <Link href="/" className="auth-back-link">Explore ProfPilot <ArrowRight className="size-4" aria-hidden="true" /></Link>
        </section>
        <div className="auth-form-area">{children}</div>
      </main>
      <SiteFooter />
    </div>
  );
}
