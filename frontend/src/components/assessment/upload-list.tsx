"use client";

import { BookOpenCheck, CircleAlert, CircleCheck, RotateCw, X } from "lucide-react";

import { FileTypeIcon } from "@/components/assessment/file-type-icon";
import type { DocumentAnalysis } from "@/components/assessment/use-document-analysis";
import type { UploadItem, UploadStatus } from "@/components/assessment/use-document-uploads";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { fileTypeLabel, formatFileSize } from "@/lib/documents/files";

const STATUS_LABELS: Record<UploadStatus, string> = {
  rejected: "Not added",
  uploading: "Uploading…",
  uploaded: "Uploaded",
  failed: "Upload failed",
  removing: "Removing…",
};

type UploadListProps = {
  items: UploadItem[];
  onRetry: (item: UploadItem) => void;
  onRemove: (item: UploadItem) => void;
  /** Whether ProfPilot has read each uploaded file (by document id), when known. */
  analysis?: DocumentAnalysis;
  onRetryAnalysis?: (documentId: string) => void;
};

/** The files added to one section, each with its type, size, and upload status. */
export function UploadList({ items, onRetry, onRemove, analysis, onRetryAnalysis }: UploadListProps) {
  return (
    <ul className="flex flex-col gap-2">
      {items.map((item) => {
        const { name, size, status, error } = item;
        const isBusy = status === "uploading" || status === "removing";
        const reading = item.documentId && status === "uploaded" ? analysis?.[item.documentId] : undefined;

        return (
          <li key={item.key} className="flex items-center gap-3 rounded-lg border bg-card p-3">
            <FileTypeIcon name={name} hasProblem={status === "rejected" || status === "failed"} />

            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium" title={name}>
                {name}
              </p>
              <p className="flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
                <span>{fileTypeLabel(name)}</span>
                <span aria-hidden>·</span>
                {size !== null && (
                  <>
                    <span>{formatFileSize(size)}</span>
                    <span aria-hidden>·</span>
                  </>
                )}
                <span role="status" className="inline-flex items-center gap-1">
                  <StatusIcon status={status} />
                  {STATUS_LABELS[status]}
                </span>
                {reading && (
                  <>
                    <span aria-hidden>·</span>
                    <span role="status" className="inline-flex items-center gap-1">
                      <ReadingIcon status={reading.status} />
                      {READING_LABELS[reading.status]}
                    </span>
                  </>
                )}
              </p>
              {error && (
                <p role="alert" className="mt-1 text-xs text-destructive">
                  {error}
                </p>
              )}
              {reading?.status === "failed" && reading.error && (
                <p className="mt-1 text-xs text-destructive">{reading.error}</p>
              )}
            </div>

            {reading?.status === "failed" && onRetryAnalysis && item.documentId && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                aria-label={`Read ${name} again`}
                onClick={() => onRetryAnalysis(item.documentId as string)}
              >
                <RotateCw />
                Read again
              </Button>
            )}

            {status === "failed" && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                aria-label={`Retry ${name}`}
                onClick={() => onRetry(item)}
              >
                <RotateCw />
                Retry
              </Button>
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              disabled={isBusy}
              aria-label={`Remove ${name}`}
              onClick={() => onRemove(item)}
            >
              <X />
            </Button>
          </li>
        );
      })}
    </ul>
  );
}

const READING_LABELS = {
  pending: "Waiting to be read",
  processing: "Being read by ProfPilot…",
  ready: "Read by ProfPilot",
  failed: "Couldn't be read",
} as const;

function ReadingIcon({ status }: { status: keyof typeof READING_LABELS }) {
  if (status === "pending" || status === "processing") return <Spinner className="size-3" aria-hidden />;
  if (status === "ready") return <BookOpenCheck className="size-3 text-emerald-600 dark:text-emerald-400" />;
  return <CircleAlert className="size-3 text-destructive" />;
}

function StatusIcon({ status }: { status: UploadStatus }) {
  if (status === "uploading" || status === "removing") {
    // Hidden from screen readers: the status text next to it says the same.
    return <Spinner className="size-3" aria-hidden />;
  }
  if (status === "uploaded") {
    return <CircleCheck className="size-3 text-emerald-600 dark:text-emerald-400" />;
  }
  return <CircleAlert className="size-3 text-destructive" />;
}
