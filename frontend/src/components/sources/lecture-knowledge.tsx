"use client";

import { BrainCircuit, ChevronDown, LoaderCircle, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { apiRequest, errorMessage } from "@/lib/api/client";
import type { LectureKnowledge } from "@/lib/api/types";
import type { CourseSource, KnowledgeState } from "@/lib/sources/types";
import { cn } from "@/lib/utils";

const CHECK_MS = 4000;

/**
 * A source's lecture knowledge: built when the file is read (a PowerPoint file's pictures read
 * first), brought up to date on its own when Sources opens, and reused by every assessment. The
 * full profile stays on the server; this shows its progress and state and, on request, a concise
 * overview. The professor never has to start anything: only Retry, after a failure.
 *
 * `preparing`: Sources is preparing it (a PowerPoint's pictures, then its knowledge); `checking`:
 * Sources is still finding out which files need that, so no action is offered yet.
 */
export function LectureKnowledgePanel({
  source,
  preparing = false,
  checking = false,
}: {
  source: CourseSource;
  preparing?: boolean;
  checking?: boolean;
}) {
  const [state, setState] = useState<KnowledgeState>(source.knowledge.state);
  const [topics, setTopics] = useState(source.knowledge.topics);
  const [details, setDetails] = useState<LectureKnowledge | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  // Whether it's being prepared or its pictures read, as last checked (until then, what Sources said).
  const [checkedWorking, setCheckedWorking] = useState<boolean | null>(null);
  const working = checkedWorking ?? preparing;
  const previousStatus = useRef(source.status);

  const load = useCallback(async () => {
    const knowledge = await apiRequest<LectureKnowledge>(`/api/documents/${source.id}/knowledge`);
    setDetails(knowledge);
    // A rebuild runs beside the working profile: show it, and keep checking until it's done.
    setState(knowledge.rebuild?.state === "building" ? "building" : knowledge.state);
    setTopics(knowledge.topics.length || null);
    setCheckedWorking(!!knowledge.preparation || knowledge.pictures?.state === "reading");
    return knowledge;
  }, [source.id]);

  // A file read during this visit has its knowledge built right after: show that.
  useEffect(() => {
    if (previousStatus.current !== "ready" && source.status === "ready") void load().catch(() => undefined);
    previousStatus.current = source.status;
  }, [source.status, load]);

  // While it's prepared, built or its pictures are read: check straight away, then now and then until it's done.
  const polling = state === "building" || working;
  useEffect(() => {
    if (!polling) return;
    const check = () => void load().catch(() => undefined);
    const first = setTimeout(check, 0);
    const timer = setInterval(check, CHECK_MS);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [polling, load]);

  /** Builds the lecture knowledge now: when it failed, or is somehow missing or out of date. */
  async function build() {
    setBusy(true);
    setProblem(null);
    try {
      // A long job: a fresh session keeps it signed in until it's done.
      await apiRequest(`/api/documents/${source.id}/knowledge`, { method: "POST", freshSession: true });
      setState("building");
    } catch (cause) {
      setProblem(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  /** Reads the lecture's pictures again, after a reading failed (pictures already read are kept). */
  async function readPictures() {
    setBusy(true);
    setProblem(null);
    try {
      await apiRequest(`/api/documents/${source.id}/figures`, { method: "POST", freshSession: true });
      setCheckedWorking(true);
    } catch (cause) {
      setProblem(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && !details) await load().catch((cause) => setProblem(errorMessage(cause)));
  }

  if (source.status !== "ready") return null;

  const readingPictures = details?.pictures?.state === "reading";
  // Being prepared, but neither its pictures nor its knowledge are in progress right now (queued, say).
  const waiting = working && !readingPictures && state !== "building";
  const quiet = checking || working;
  const action = quiet ? null : state === "none" ? "Build lecture knowledge" : state === "outdated" ? "Rebuild" : state === "failed" ? "Retry" : null;
  const picturesError = quiet ? null : (details?.pictures?.error ?? null);
  const rebuildFailed = state === "ready" && !quiet && details?.rebuild?.state === "failed";
  return (
    <div className="mt-1.5 flex flex-col gap-1.5 text-[11px]" data-knowledge-open={open && details ? "" : undefined}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span
          className={cn(
            "inline-flex items-center gap-1",
            state === "failed" && !working ? "text-destructive" : "text-muted-foreground",
          )}
        >
          {readingPictures || waiting || state === "building" ? (
            <LoaderCircle className="size-3 animate-spin" />
          ) : state === "failed" ? (
            <TriangleAlert className="size-3" />
          ) : (
            <BrainCircuit className="size-3" />
          )}
          {readingPictures && "Reading the pictures in this lecture…"}
          {waiting && "Preparing for AI…"}
          {!readingPictures &&
            state === "building" &&
            (topics ? "Rebuilding lecture knowledge… (the current version stays in use)" : "Building lecture knowledge…")}
          {!working && state === "ready" && `Lecture knowledge · ${topics ?? 0} ${topics === 1 ? "topic" : "topics"}`}
          {!working && state === "none" && "No lecture knowledge yet"}
          {!working && state === "outdated" && "Lecture knowledge is out of date"}
          {!working && state === "failed" && (details?.error ?? "Building the lecture knowledge failed")}
        </span>
        {action && (
          <Button variant="link" size="sm" className="h-auto p-0 text-[11px]" disabled={busy} onClick={() => void build()}>
            {action}
          </Button>
        )}
        {!working && (state === "ready" || state === "outdated") && (
          <Button
            variant="link"
            size="sm"
            className="h-auto p-0 text-[11px]"
            aria-expanded={open}
            onClick={() => void toggle()}
          >
            {open ? "Hide" : "View"}
            <ChevronDown className={cn("size-3 motion-safe:transition-transform", open && "rotate-180")} aria-hidden="true" />
          </Button>
        )}
      </div>
      {picturesError && (
        <p role="alert" className="flex flex-wrap items-center gap-x-2 text-destructive">
          Reading the pictures failed: {picturesError}
          <Button variant="link" size="sm" className="h-auto p-0 text-[11px]" disabled={busy} onClick={() => void readPictures()}>
            Try again
          </Button>
        </p>
      )}
      {problem && (
        <p role="alert" className="text-destructive">
          {problem}
        </p>
      )}
      {open && rebuildFailed && (
        <div className="flex flex-col gap-1 rounded-lg border border-dashed p-2">
          <p className="text-destructive">
            Rebuilding failed: {details?.rebuild?.error ?? "try again"} The previous lecture knowledge is still in use.
          </p>
          <div className="flex flex-wrap gap-x-3">
            <Button variant="link" size="sm" className="h-auto p-0 text-[11px]" disabled={busy} onClick={() => void build()}>
              Retry rebuild
            </Button>
          </div>
        </div>
      )}
      {open && details && <KnowledgeOverview knowledge={details} />}
    </div>
  );
}

/** Honest coverage: substantive pages by class when known, otherwise only which pages had any text. */
function CoverageLine({ coverage, unit }: { coverage: NonNullable<LectureKnowledge["coverage"]>; unit: string }) {
  const pages = coverage.pages ?? {};
  const listed = (label: string, numbers: number[] | undefined) =>
    numbers && numbers.length > 0 ? ` · ${label} ${unit}s ${numbers.join(", ")}` : "";
  // A lecture PDF's pictures aren't in its words, but the AI sees each slide itself as it writes from it.
  const pictures = unit === "page" ? "pictures (seen as questions are written) on" : "figures not readable on";
  if (coverage.pageDetail && coverage.substantiveUnits != null) {
    return (
      <p className="text-muted-foreground">
        {coverage.substantiveUnits} of {coverage.totalUnits} {unit}s have substantive text
        {listed(pictures, pages.textWithFigures)}
        {listed("pictures read on", coverage.unitsWithReadFigures)}
        {listed("mostly figures on", pages.figureDominated)}
        {listed("title or divider", pages.divider)}
        {listed("no text on", pages.empty)}
        {listed("formulas unverified on", pages.uncertainMath)}
        {coverage.truncated && " · end of file not read"}
      </p>
    );
  }
  return (
    <p className="text-muted-foreground">
      {coverage.analysedUnits} of {coverage.totalUnits} {unit}s have extracted text (page-level detail unavailable)
      {listed("no text on", coverage.unitsWithoutText)}
      {listed(pictures, coverage.unitsWithFigures)}
      {coverage.truncated && " · end of file not read"}
    </p>
  );
}

function KnowledgeOverview({ knowledge }: { knowledge: LectureKnowledge }) {
  const { coverage } = knowledge;
  const unit = coverage?.unit ?? "page";
  return (
    <div className="flex flex-col gap-2 rounded-lg border bg-muted/30 p-3 text-xs">
      {knowledge.title && <p className="font-medium">{knowledge.title}</p>}
      {knowledge.overview && <p className="text-muted-foreground">{knowledge.overview}</p>}
      {coverage?.totalUnits ? <CoverageLine coverage={coverage} unit={unit} /> : null}
      {knowledge.sections.length > 0 && (
        <p>
          <span className="font-medium">Sections: </span>
          {knowledge.sections.join(" → ")}
        </p>
      )}
      {knowledge.topics.length > 0 && (
        <ul className="max-h-48 list-disc overflow-y-auto pl-4 md:max-h-none md:columns-2 md:gap-8 [&>li]:break-inside-avoid">
          {knowledge.topics.map((topic) => (
            <li key={topic.id}>
              {topic.name}
              {topic.locations.length > 0 && <span className="text-muted-foreground"> ({topic.locations.join(", ")})</span>}
            </li>
          ))}
        </ul>
      )}
      {knowledge.warnings.length > 0 && (
        <ul className="flex flex-col gap-1 text-amber-700 dark:text-amber-400">
          {knowledge.warnings.map((warning) => (
            <li key={warning} className="flex gap-1">
              <TriangleAlert className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
              {warning}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
