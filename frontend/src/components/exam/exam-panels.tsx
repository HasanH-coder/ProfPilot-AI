"use client";

import {
  AlertTriangle,
  Ban,
  CircleAlert,
  CircleCheck,
  Download,
  Flag,
  Info,
  RefreshCw,
  ShieldCheck,
  Wrench,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useState, type ReactNode } from "react";

import { DifficultyBar } from "@/components/assessment/difficulty-bar";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { apiDownload, apiRequest, errorMessage } from "@/lib/api/client";
import type { Exam, ExamVersion, Readiness, ReadinessCheck } from "@/lib/api/types";
import { timeAgo } from "@/lib/time";
import { cn } from "@/lib/utils";

function Panel({ title, icon, children, action }: { title: string; icon: ReactNode; children: ReactNode; action?: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3 rounded-xl border bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 id={id} className="flex items-center gap-2 text-sm font-semibold">
          {icon}
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  );
}

// -----------------------------------------------------------------------------
// Distribution
// -----------------------------------------------------------------------------

export function DistributionPanel({ exam, version }: { exam: Exam; version: ExamVersion }) {
  const { summary, warnings } = version;
  const spec = exam.spec;
  const rows: { label: string; value: string; target?: string }[] = [
    { label: "Questions", value: String(summary.questionCount) },
    { label: "Total marks", value: String(summary.totalPoints), target: spec?.total_points ? String(spec.total_points) : undefined },
    {
      label: "Estimated time",
      value: `~${summary.estimatedMinutes} min`,
      target: spec?.duration_minutes ? `${spec.duration_minutes} min` : undefined,
    },
    {
      label: "Multiple choice",
      value: `${summary.mcqPercent}% of marks`,
      target: spec?.question_format ? `${spec.question_format.mcq_percent}%` : undefined,
    },
  ];
  return (
    <Panel title={exam.versions.length > 1 ? `Version ${version.label} at a glance` : "At a glance"} icon={<Info className="size-4" />}>
      <dl className="flex flex-col gap-1.5 text-sm">
        {rows.map((row) => (
          <div key={row.label} className="flex items-baseline justify-between gap-3">
            <dt className="text-muted-foreground">{row.label}</dt>
            <dd className="text-right">
              {row.value}
              {row.target && <span className="text-muted-foreground"> · target {row.target}</span>}
            </dd>
          </div>
        ))}
      </dl>
      <div className="flex flex-col gap-1.5 text-sm">
        <p className="text-muted-foreground">Difficulty by marks</p>
        <DifficultyBar percentages={{ easy: summary.easyPercent, medium: summary.mediumPercent, hard: summary.hardPercent }} />
        <p className="text-xs text-muted-foreground">
          {summary.easyPercent}% easy · {summary.mediumPercent}% medium · {summary.hardPercent}% hard
          {spec?.difficulty &&
            ` (target ${spec.difficulty.easy_percent}/${spec.difficulty.medium_percent}/${spec.difficulty.hard_percent})`}
        </p>
      </div>
      {warnings.length > 0 && (
        <ul className="flex flex-col gap-1.5 text-sm">
          {warnings.map((warning) => (
            <li key={`${warning.dimension}-${warning.label}`} className="flex items-start gap-1.5 text-amber-700 dark:text-amber-400">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              {warning.message}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

// -----------------------------------------------------------------------------
// AI quality check
// -----------------------------------------------------------------------------

const REVIEW_BADGES: Record<Exam["reviewStatus"], { label: string; className: string }> = {
  passed: { label: "Passed", className: "border-emerald-600/40 text-emerald-700 dark:text-emerald-400" },
  needs_attention: { label: "Needs attention", className: "border-amber-500/50 text-amber-700 dark:text-amber-400" },
  running: { label: "Running", className: "" },
  not_run: { label: "Not run", className: "text-muted-foreground" },
  failed: { label: "Didn't finish", className: "border-destructive/40 text-destructive" },
};

export function QualityCheckPanel({
  exam,
  versionLabel,
  running,
  onRun,
}: {
  exam: Exam;
  versionLabel: string;
  running: boolean;
  onRun: () => void;
}) {
  const review = exam.review;
  const badge = REVIEW_BADGES[running ? "running" : exam.reviewStatus];
  const issues = (review?.issues ?? []).filter((issue) => !issue.versionLabel || issue.versionLabel === versionLabel);
  const order = { critical: 0, warning: 1, info: 2 } as const;
  issues.sort((a, b) => order[a.severity] - order[b.severity]);

  return (
    <Panel
      title="AI quality check"
      icon={<ShieldCheck className="size-4" />}
      action={
        <Badge variant="outline" className={badge.className}>
          {running && <Spinner className="size-3" />}
          {badge.label}
        </Badge>
      }
    >
      {review ? (
        <>
          <p className="text-sm text-muted-foreground">{review.summary}</p>
          {review.fixesApplied > 0 && (
            <p className="flex items-center gap-1.5 text-sm">
              <Wrench className="size-3.5 shrink-0" />
              {review.fixesApplied} small {review.fixesApplied === 1 ? "problem was" : "problems were"} fixed automatically
              (see each question&apos;s history).
            </p>
          )}
          {issues.length > 0 ? (
            <ul className="flex max-h-80 flex-col gap-2 overflow-y-auto text-sm">
              {issues.map((issue) => (
                <li key={issue.id} className="flex items-start gap-2">
                  {issue.severity === "critical" ? (
                    <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-destructive" aria-label="Critical" />
                  ) : issue.severity === "warning" ? (
                    <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-amber-600" aria-label="Warning" />
                  ) : (
                    <Info className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-label="Suggestion" />
                  )}
                  <span className="min-w-0">
                    {issue.questionId && issue.questionNumber && (
                      <a href={`#question-${issue.questionId}`} className="font-medium underline-offset-4 hover:underline">
                        Question {issue.questionNumber}:{" "}
                      </a>
                    )}
                    {issue.message}
                    {issue.fixApplied && (
                      <Badge variant="secondary" className="ml-1.5">
                        Fixed
                      </Badge>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="flex items-center gap-1.5 text-sm">
              <CircleCheck className="size-3.5 text-emerald-600 dark:text-emerald-400" />
              No open issues for this version.
            </p>
          )}
          {exam.reviewedAt && <p className="text-xs text-muted-foreground">Checked {timeAgo(exam.reviewedAt)}</p>}
        </>
      ) : (
        <p className="text-sm text-muted-foreground">
          ProfPilot checks answer keys, clarity, duplication, coverage, timing and how well the exam matches your plan.
        </p>
      )}
      <Button type="button" variant="outline" size="sm" className="w-fit" disabled={running} onClick={onRun}>
        {running ? <Spinner /> : <RefreshCw />}
        {review ? "Run the check again" : "Run quality check"}
      </Button>
    </Panel>
  );
}

// -----------------------------------------------------------------------------
// Final review and export
// -----------------------------------------------------------------------------

const CHECK_ICONS: Record<ReadinessCheck["status"], ReactNode> = {
  ok: <CircleCheck className="size-3.5 text-emerald-600 dark:text-emerald-400" aria-label="Done" />,
  info: <Info className="size-3.5 text-muted-foreground" aria-label="Note" />,
  warning: <AlertTriangle className="size-3.5 text-amber-600" aria-label="Warning" />,
  serious: <CircleAlert className="size-3.5 text-destructive" aria-label="Needs confirmation" />,
  blocked: <Ban className="size-3.5 text-destructive" aria-label="Blocked" />,
};

const EXPORT_KINDS = { student: "Student exam", answer_key: "Answer key", both: "Both" };

export function FinalReviewPanel({ exam, onFinalized }: { exam: Exam; onFinalized: () => void }) {
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState<keyof typeof EXPORT_KINDS>("student");
  const [version, setVersion] = useState("all");
  const [downloading, setDownloading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const kindId = useId();
  const versionId = useId();
  const multi = exam.versions.length > 1;
  const versionItems: Record<string, string> = {
    all: multi ? "All versions" : `Version ${exam.versions[0]?.label ?? "A"}`,
    ...Object.fromEntries(exam.versions.map((item) => [item.label, `Version ${item.label}`])),
  };

  useEffect(() => {
    let cancelled = false;
    apiRequest<Readiness>(`/api/exams/${exam.id}/readiness`)
      .then((result) => !cancelled && setReadiness(result))
      .catch((cause) => !cancelled && setError(errorMessage(cause)));
    return () => {
      cancelled = true;
    };
  }, [exam.id, exam.updatedAt, exam.questions]);

  async function download(confirm: boolean) {
    setDownloading(true);
    setError(null);
    try {
      await apiDownload(`/api/exams/${exam.id}/export?kind=${kind}&version=${version}${confirm ? "&confirm=true" : ""}`);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setDownloading(false);
    }
  }

  async function finalize() {
    setFinalizing(true);
    setError(null);
    try {
      await apiRequest(`/api/exams/${exam.id}/finalization`, { method: "POST" });
      onFinalized();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setFinalizing(false);
    }
  }

  const serious = readiness?.checks.filter((check) => check.status === "serious") ?? [];
  return (
    <Panel title="Final review & export" icon={<Flag className="size-4" />}>
      {!readiness && !error ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-4/5" />
          <Skeleton className="h-4 w-3/5" />
        </div>
      ) : readiness ? (
        <ul className="flex flex-col gap-1.5 text-sm">
          {readiness.checks.map((check) => (
            <li key={check.id} className="flex items-start gap-2">
              <span className="mt-0.5 shrink-0">{CHECK_ICONS[check.status]}</span>
              <span className="min-w-0">
                {check.label}
                {check.detail && <span className="block text-xs text-muted-foreground">{check.detail}</span>}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={kindId}>Export</Label>
          <Select items={EXPORT_KINDS} value={kind} onValueChange={(value) => value && setKind(value as keyof typeof EXPORT_KINDS)}>
            <SelectTrigger id={kindId} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(EXPORT_KINDS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {multi && (
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={versionId}>Version</Label>
            <Select items={versionItems} value={version} onValueChange={(value) => value && setVersion(value)}>
              <SelectTrigger id={versionId} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(versionItems).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {kind === "student"
          ? "The student paper never includes answers, solutions or notes."
          : kind === "answer_key"
            ? "Answers, worked solutions, rubrics and marks, for markers only."
            : "A ZIP with the student paper and the answer key."}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          disabled={downloading || !readiness?.canExport}
          onClick={() => (readiness?.requiresConfirmation ? setConfirming(true) : void download(false))}
        >
          {downloading ? <Spinner /> : <Download />}
          Download PDF
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={finalizing || !readiness?.canExport || Boolean(exam.finalizedAt)}
          onClick={() => void finalize()}
        >
          {finalizing ? <Spinner /> : <Flag />}
          {exam.finalizedAt ? "Marked as final" : "Mark as final"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        {exam.finalizedAt
          ? `Marked final ${timeAgo(exam.finalizedAt)}. `
          : "Marking an exam final lets ProfPilot learn your typical choices. "}
        <Link href="/workspace/preferences" className="underline underline-offset-4">
          Manage preferences
        </Link>
      </p>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Export anyway?</AlertDialogTitle>
            <AlertDialogDescription render={<div />}>
              <p>These need your attention before the exam is printed:</p>
              <ul className={cn("mt-2 list-disc pl-5")}>
                {serious.map((check) => (
                  <li key={check.id}>
                    {check.label}
                    {check.detail ? ` (${check.detail})` : ""}
                  </li>
                ))}
              </ul>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Go back</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirming(false);
                void download(true);
              }}
            >
              Export anyway
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Panel>
  );
}
