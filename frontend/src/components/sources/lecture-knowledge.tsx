"use client";

import { BrainCircuit, ChevronDown, LoaderCircle, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { apiRequest, errorMessage } from "@/lib/api/client";
import type { LectureKnowledge } from "@/lib/api/types";
import type { CourseSource, KnowledgeState } from "@/lib/sources/types";
import { cn } from "@/lib/utils";

const CHECK_MS = 4000;

const formatTokens = (tokens: number) => (tokens >= 1000 ? `${Math.round(tokens / 1000)}k` : String(tokens));

/**
 * A source's lecture knowledge: built once (when the file is read, or on request
 * with an estimate first) and reused by every assessment. The full profile stays
 * on the server; this shows its state and, on request, a concise overview.
 */
export function LectureKnowledgePanel({ source }: { source: CourseSource }) {
  const [state, setState] = useState<KnowledgeState>(source.knowledge.state);
  const [topics, setTopics] = useState(source.knowledge.topics);
  const [details, setDetails] = useState<LectureKnowledge | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const previousStatus = useRef(source.status);

  const load = useCallback(async () => {
    const knowledge = await apiRequest<LectureKnowledge>(`/api/documents/${source.id}/knowledge`);
    setDetails(knowledge);
    // A rebuild runs beside the working profile: show it, and keep checking until it's done.
    setState(knowledge.rebuild?.state === "building" ? "building" : knowledge.state);
    setTopics(knowledge.topics.length || null);
    return knowledge;
  }, [source.id]);

  // A file read during this visit builds its knowledge right after: show that.
  useEffect(() => {
    if (previousStatus.current !== "ready" && source.status === "ready") void load().catch(() => undefined);
    previousStatus.current = source.status;
  }, [source.status, load]);

  // While it builds, check back now and then until it's done.
  useEffect(() => {
    if (state !== "building") return;
    const timer = setInterval(() => void load().catch(() => undefined), CHECK_MS);
    return () => clearInterval(timer);
  }, [state, load]);

  async function build() {
    setBusy(true);
    setProblem(null);
    try {
      const { estimate, topics: existing } = details ?? (await load());
      const confirmed = window.confirm(
        `Analyse “${source.name}” in full to build its lecture knowledge?\n\n` +
          `Estimated cost: ${estimate.modelCalls} AI calls, about ${formatTokens(estimate.inputTokens)} input and ` +
          `${formatTokens(estimate.outputTokens)} output tokens. It's done once and reused by every assessment.` +
          (existing.length > 0
            ? "\n\nThe current lecture knowledge stays in use until the new one is ready, and is kept if this fails."
            : ""),
      );
      if (!confirmed) return;
      await apiRequest(`/api/documents/${source.id}/knowledge`, { method: "POST" });
      setState("building");
    } catch (cause) {
      setProblem(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  /** The free first step of a refresh: re-read the file's text with page-level detail. */
  async function reread() {
    setBusy(true);
    setProblem(null);
    try {
      const { rereadEstimate } = details ?? (await load());
      const confirmed = window.confirm(
        `Re-read “${source.name}” to add page-level detail?\n\n` +
          `No AI analysis is done: only the new text is indexed for search (one embedding request, about ` +
          `${formatTokens(rereadEstimate?.embeddingTokens ?? 0)} tokens, well under one cent). The file, its ` +
          "assessments and its current lecture knowledge stay as they are. Rebuilding the lecture knowledge is a " +
          "separate step that asks first.",
      );
      if (!confirmed) return;
      await apiRequest(`/api/documents/${source.id}/extraction`, { method: "POST" });
      await load();
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

  const action =
    state === "none" ? "Build lecture knowledge" : state === "outdated" ? "Rebuild" : state === "failed" ? "Retry" : null;
  const extraction = details?.extraction;
  // Refresh, in two steps: a free re-read for page markers, then a paid rebuild that asks first.
  const needsReread =
    state !== "building" && !!extraction?.rereadable && (extraction.state === "legacy" || extraction.state === "interrupted");
  const needsRebuild = state === "ready" && !!details?.legacy && extraction?.state === "current";
  const rebuildFailed = state === "ready" && details?.rebuild?.state === "failed";
  return (
    <div className="mt-1.5 flex flex-col gap-1.5 text-[11px]" data-knowledge-open={open && details ? "" : undefined}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className={cn("inline-flex items-center gap-1", state === "failed" ? "text-destructive" : "text-muted-foreground")}>
          {state === "building" ? (
            <LoaderCircle className="size-3 animate-spin" />
          ) : state === "failed" ? (
            <TriangleAlert className="size-3" />
          ) : (
            <BrainCircuit className="size-3" />
          )}
          {state === "building" &&
            (topics ? "Rebuilding lecture knowledge… (the current version stays in use)" : "Building lecture knowledge…")}
          {state === "ready" && `Lecture knowledge · ${topics ?? 0} ${topics === 1 ? "topic" : "topics"}`}
          {state === "none" && "No lecture knowledge yet"}
          {state === "outdated" && "Lecture knowledge is out of date"}
          {state === "failed" && (details?.error ?? "Building the lecture knowledge failed")}
        </span>
        {action && (
          <Button variant="link" size="sm" className="h-auto p-0 text-[11px]" disabled={busy} onClick={() => void build()}>
            {action}
          </Button>
        )}
        {(state === "ready" || state === "outdated") && (
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
      {problem && (
        <p role="alert" className="text-destructive">
          {problem}
        </p>
      )}
      {open && (needsReread || needsRebuild || rebuildFailed) && (
        <div className="flex flex-col gap-1 rounded-lg border border-dashed p-2">
          {rebuildFailed && (
            <p className="text-destructive">
              Rebuilding failed: {details?.rebuild?.error ?? "try again"} The previous lecture knowledge is still in use.
            </p>
          )}
          {needsReread && (
            <p className="text-muted-foreground">
              {extraction?.state === "interrupted"
                ? "Re-reading this file was interrupted. Re-read it to finish."
                : "This file was read before page-level detail existed. Step 1 (free): re-read it."}
            </p>
          )}
          {needsRebuild && !rebuildFailed && (
            <p className="text-muted-foreground">
              The text now has page-level detail. Step 2: rebuild the lecture knowledge to use it.
            </p>
          )}
          <div className="flex flex-wrap gap-x-3">
            {needsReread && (
              <Button variant="link" size="sm" className="h-auto p-0 text-[11px]" disabled={busy} onClick={() => void reread()}>
                Re-read file
              </Button>
            )}
            {(needsRebuild || rebuildFailed) && !needsReread && (
              <Button variant="link" size="sm" className="h-auto p-0 text-[11px]" disabled={busy} onClick={() => void build()}>
                {rebuildFailed ? "Retry rebuild" : "Rebuild lecture knowledge"}
              </Button>
            )}
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
  if (coverage.pageDetail && coverage.substantiveUnits != null) {
    return (
      <p className="text-muted-foreground">
        {coverage.substantiveUnits} of {coverage.totalUnits} {unit}s have substantive text
        {listed("figures not readable on", pages.textWithFigures)}
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
      {listed("figures not readable on", coverage.unitsWithFigures)}
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
