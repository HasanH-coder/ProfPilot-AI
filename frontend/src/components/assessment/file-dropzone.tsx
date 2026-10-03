"use client";

import { Upload } from "lucide-react";
import { useId, useState, type DragEvent } from "react";

import { ACCEPTED_EXTENSIONS, SUPPORTED_TYPES_TEXT } from "@/lib/documents/files";
import { cn } from "@/lib/utils";

type FileDropzoneProps = {
  /** id of the element that names the dropzone, such as its section title. */
  labelledBy: string;
  /** id of an element that describes the dropzone, such as the section's helper text. */
  describedBy?: string;
  /** Set when files can't be added yet. Explains why. */
  disabledReason?: string;
  onFiles: (files: File[]) => void;
};

/** An area to drop files on, or to click (or press Enter or Space on) to choose files. */
export function FileDropzone({ labelledBy, describedBy, disabledReason, onFiles }: FileDropzoneProps) {
  const hintId = useId();
  const [isDragging, setIsDragging] = useState(false);
  const disabled = Boolean(disabledReason);

  function handleDragOver(event: DragEvent<HTMLLabelElement>) {
    // Accept the drop here, so the browser doesn't open the file in place of the form.
    event.preventDefault();
    event.dataTransfer.dropEffect = disabled ? "none" : "copy";
    if (!disabled) setIsDragging(true);
  }

  function handleDragLeave(event: DragEvent<HTMLLabelElement>) {
    // Moving onto the dropzone's own icon or text also counts as leaving; ignore that.
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setIsDragging(false);
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setIsDragging(false);
    if (!disabled) onFiles(Array.from(event.dataTransfer.files));
  }

  return (
    <label
      onDragEnter={handleDragOver}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={cn(
        "flex flex-col items-center gap-2 rounded-xl border border-dashed px-6 py-8 text-center transition-colors",
        "has-[:focus-visible]:border-ring has-[:focus-visible]:ring-3 has-[:focus-visible]:ring-ring/50",
        disabled
          ? "cursor-not-allowed bg-muted/30"
          : "cursor-pointer hover:border-foreground/25 hover:bg-muted/40",
        isDragging && "border-primary bg-muted/60",
      )}
    >
      <input
        type="file"
        multiple
        accept={ACCEPTED_EXTENSIONS}
        disabled={disabled}
        className="sr-only"
        aria-labelledby={labelledBy}
        aria-describedby={describedBy ? `${describedBy} ${hintId}` : hintId}
        onChange={(event) => {
          onFiles(Array.from(event.target.files ?? []));
          // Clear the choice, so the same file can be chosen again later.
          event.target.value = "";
        }}
      />
      <span
        className={cn(
          "flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground",
          disabled && "opacity-60",
        )}
      >
        <Upload className="size-5" />
      </span>
      <span className={cn("text-sm font-medium", disabled && "text-muted-foreground")}>
        Drag files here or <span className={cn(!disabled && "underline underline-offset-4")}>browse</span>
      </span>
      <span id={hintId} className="text-xs text-balance text-muted-foreground">
        {disabledReason ?? `${SUPPORTED_TYPES_TEXT}. Up to 25 MB each.`}
      </span>
    </label>
  );
}
