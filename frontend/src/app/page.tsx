import { ArrowRight, BookOpen, ClipboardCheck, Headphones } from "lucide-react";
import Image from "next/image";
import Link from "next/link";

import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const steps = [
  { icon: BookOpen, title: "Bring your course material", text: "Start with your lectures, notes, and previous exams. ProfPilot works from what you provide." },
  { icon: Headphones, title: "Choose how to create", text: "Generate a complete exam or talk with your assistant while you build it question by question." },
  { icon: ClipboardCheck, title: "Make it yours", text: "Review every question, refine the answers, and download your exam and answer key." },
];

export default function HomePage() {
  return (
    <div className="public-theme public-shell">
      <SiteHeader>
        <Link href="/login" className={cn(buttonVariants({ variant: "ghost" }), "hidden sm:inline-flex")}>
          Log in
        </Link>
        <Link href="/signup" className={buttonVariants()}>
          Sign up
        </Link>
      </SiteHeader>

      <main id="main-content" className="public-landing-main">
        <section className="public-hero" aria-labelledby="hero-title">
          <div className="public-hero-copy">
            <p className="public-eyebrow">PROFPILOT AI · THE PROFESSOR WORKSPACE</p>
            <h1 id="hero-title" className="public-display public-hero-title">
              Your courses.<br />Your expertise.<br /><span>A little more time.</span>
            </h1>
            <p className="public-hero-description">
              An intelligent workspace for your teaching. Create thoughtful assessments from your course materials,
              with an AI assistant by your side.
            </p>
            <div className="flex flex-wrap gap-3">
              <Link href="/signup" className={buttonVariants({ size: "lg" })}>
                Create your workspace <ArrowRight data-icon="inline-end" aria-hidden="true" />
              </Link>
              <Link href="/login" className={buttonVariants({ size: "lg", variant: "outline" })}>Log in</Link>
            </div>
            <p className="public-hero-note">Your judgment leads. ProfPilot helps you get there.</p>
          </div>
          <div className="public-campus-card">
            <div className="public-campus-photo">
              <Image src="/images/aub-college-hall.jpg" alt="College Hall at the American University of Beirut" fill sizes="(max-width: 900px) 100vw, 480px" className="object-cover" />
              <span className="public-campus-label">AMERICAN UNIVERSITY OF BEIRUT</span>
            </div>
            <div className="public-campus-caption">
              <span className="public-icon"><ClipboardCheck aria-hidden="true" /></span>
              <div><p className="public-display">A familiar place. A new way to work.</p><span>From the first idea to the final assessment.</span></div>
            </div>
          </div>
        </section>
        <section className="public-steps" aria-labelledby="steps-title">
          <div className="public-steps-heading">
            <h2 id="steps-title" className="public-display">Thoughtful assessments, from start to finish.</h2>
            <p>One connected workflow. You stay in control at every step.</p>
          </div>
          <div className="public-steps-grid">
            {steps.map(({ icon: Icon, title, text }, index) => (
              <article key={title} className="public-step">
                <div className="flex items-center justify-between"><span className="public-icon"><Icon aria-hidden="true" /></span><span className="public-step-number">0{index + 1}</span></div>
                <h3>{title}</h3><p>{text}</p>
              </article>
            ))}
          </div>
        </section>
      </main>

      <SiteFooter />
    </div>
  );
}
