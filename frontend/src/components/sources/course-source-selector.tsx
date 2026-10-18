"use client";

import { Check, FileText, Search } from "lucide-react";
import { useId, useMemo, useState } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { fileTypeLabel, formatFileSize } from "@/lib/documents/files";
import type { CourseSource } from "@/lib/sources/types";
import { cn } from "@/lib/utils";

type CourseSourceSelectorProps = {
  sources: CourseSource[];
  selectedIds: string[];
  onChange: (documentIds: string[]) => void;
  disabled?: boolean;
};

/** Controlled selector ready to embed in any agent workflow, including assessment setup. */
export function CourseSourceSelector({ sources, selectedIds, onChange, disabled }: CourseSourceSelectorProps) {
  const searchId = useId();
  const [query, setQuery] = useState("");
  const selected = useMemo(() => new Set(selectedIds), [selectedIds]);
  const available = sources.filter((source) => source.status === "ready");
  const filtered = available.filter((source) =>
    [source.name, source.title].filter(Boolean).join(" ").toLowerCase().includes(query.trim().toLowerCase()),
  );

  function toggle(id: string, checked: boolean) {
    const next = new Set(selected);
    if (checked) next.add(id);
    else next.delete(id);
    onChange(available.filter((source) => next.has(source.id)).map((source) => source.id));
  }

  if (!available.length) {
    return (
      <div className="rounded-xl border border-dashed bg-muted/20 px-5 py-8 text-center">
        <FileText className="mx-auto mb-2 size-6 text-primary" />
        <p className="text-sm font-medium">No ready course sources</p>
        <p className="mt-1 text-xs text-muted-foreground">Upload material in Sources, then select it here.</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="relative">
        <label htmlFor={searchId} className="sr-only">Search course sources</label>
        <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          id={searchId}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search course sources"
          className="pl-9"
          disabled={disabled}
        />
      </div>
      <div className="max-h-80 space-y-2 overflow-y-auto pr-1" role="group" aria-label="Course sources">
        {filtered.map((source) => {
          const checked = selected.has(source.id);
          return (
            <label
              key={source.id}
              className={cn(
                "flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition-colors",
                checked ? "border-primary/40 bg-accent/60" : "hover:border-primary/25 hover:bg-muted/30",
                disabled && "cursor-not-allowed opacity-60",
              )}
            >
              <Checkbox
                checked={checked}
                onCheckedChange={(value) => toggle(source.id, value === true)}
                disabled={disabled}
                aria-label={`Select ${source.name}`}
                className="mt-0.5"
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{source.title || source.name}</span>
                <span className="block truncate text-xs text-muted-foreground">
                  {source.title ? `${source.name} · ` : ""}
                  {fileTypeLabel(source.name)}
                  {source.size !== null ? ` · ${formatFileSize(source.size)}` : ""}
                </span>
              </span>
              {checked && <Check className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />}
            </label>
          );
        })}
        {!filtered.length && <p className="py-6 text-center text-sm text-muted-foreground">No matching sources.</p>}
      </div>
      <p className="text-xs text-muted-foreground" aria-live="polite">
        {selected.size} {selected.size === 1 ? "source" : "sources"} selected
      </p>
    </div>
  );
}
