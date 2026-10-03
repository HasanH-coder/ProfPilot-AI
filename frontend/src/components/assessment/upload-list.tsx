"use client";

import {
  CircleAlert,
  CircleCheck,
  FileIcon,
  FileImage,
  FileText,
  Presentation,
  RotateCw,
  X,
} from "lucide-react";

import type { UploadItem, UploadStatus } from "@/components/assessment/use-document-uploads";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { fileExtension, fileTypeLabel, formatFileSize } from "@/lib/documents/files";
import { cn } from "@/lib/utils";

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
};

/** The files added to one section, each with its type, size, and upload status. */
export function UploadList({ items, onRetry, onRemove }: UploadListProps) {
  return (
    <ul className="flex flex-col gap-2">
      {items.map((item) => {
        const { file, status, error } = item;
        const isBusy = status === "uploading" || status === "removing";
        const hasProblem = status === "rejected" || status === "failed";

        return (
          <li key={item.key} className="flex items-center gap-3 rounded-lg border bg-card p-3">
            <span
              className={cn(
                "flex size-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground",
                hasProblem && "bg-destructive/10 text-destructive",
              )}
            >
              <FileTypeIcon name={file.name} />
            </span>

            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium" title={file.name}>
                {file.name}
              </p>
              <p className="flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
                <span>{fileTypeLabel(file.name)}</span>
                <span aria-hidden>·</span>
                <span>{formatFileSize(file.size)}</span>
                <span aria-hidden>·</span>
                <span role="status" className="inline-flex items-center gap-1">
                  <StatusIcon status={status} />
                  {STATUS_LABELS[status]}
                </span>
              </p>
              {error && (
                <p role="alert" className="mt-1 text-xs text-destructive">
                  {error}
                </p>
              )}
            </div>

            {status === "failed" && (
              <Button type="button" variant="outline" size="sm" onClick={() => onRetry(item)}>
                <RotateCw />
                Retry
              </Button>
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              disabled={isBusy}
              aria-label={`Remove ${file.name}`}
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

function FileTypeIcon({ name }: { name: string }) {
  const extension = fileExtension(name);
  if (extension === "pptx") return <Presentation className="size-4.5" />;
  if (["png", "jpg", "jpeg", "webp"].includes(extension)) return <FileImage className="size-4.5" />;
  if (["pdf", "docx", "txt"].includes(extension)) return <FileText className="size-4.5" />;
  return <FileIcon className="size-4.5" />;
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
