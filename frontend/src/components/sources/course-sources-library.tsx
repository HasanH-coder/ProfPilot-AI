"use client";

import { ExternalLink, FileCheck2, LoaderCircle, RotateCcw, Trash2, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";

import { FileDropzone } from "@/components/assessment/file-dropzone";
import { FileTypeIcon } from "@/components/assessment/file-type-icon";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { apiRequest, errorMessage } from "@/lib/api/client";
import { ASSESSMENT_FILES_BUCKET, checkFile, fileTypeLabel, formatFileSize, safeFileName } from "@/lib/documents/files";
import {
  findCourseSource,
  getCourseSourceUrl,
  recordCourseSource,
  removeCourseSource,
} from "@/lib/sources/actions";
import type { CourseSource, CourseWithSources, SourceStatus } from "@/lib/sources/types";
import { createClient } from "@/lib/supabase/client";

type StatusReply = { status: SourceStatus; error: string | null; pageCount: number | null; title: string | null };

export function CourseSourcesLibrary({ initialCourses, professorId }: { initialCourses: CourseWithSources[]; professorId: string }) {
  const [courses, setCourses] = useState(initialCourses);
  const [activity, setActivity] = useState<Record<string, string>>({});

  function updateSource(courseId: string, source: CourseSource) {
    setCourses((current) =>
      current.map((course) =>
        course.id !== courseId
          ? course
          : { ...course, sources: [source, ...course.sources.filter((item) => item.id !== source.id)] },
      ),
    );
  }

  async function addFiles(courseId: string, files: File[]) {
    for (const file of files) {
      const checked = checkFile(file);
      if (checked.error) {
        setActivity((current) => ({ ...current, [courseId]: checked.error }));
        continue;
      }
      setActivity((current) => ({ ...current, [courseId]: `Preparing ${file.name}…` }));
      try {
        const sha256 = await digestFile(file);
        const duplicate = await findCourseSource(courseId, sha256);
        if (duplicate.source) {
          updateSource(courseId, duplicate.source);
          if (duplicate.source.status === "ready") {
            setActivity((current) => ({ ...current, [courseId]: `${file.name} is already in this course.` }));
          } else {
            void startProcessing(courseId, duplicate.source);
          }
          continue;
        }
        const storagePath = `${professorId}/${courseId}/sources/${crypto.randomUUID()}-${safeFileName(file.name)}`;
        const typedFile = new File([file], file.name, { type: checked.contentType });
        setActivity((current) => ({ ...current, [courseId]: `Uploading ${file.name}…` }));
        const storage = createClient().storage.from(ASSESSMENT_FILES_BUCKET);
        const { error: uploadError } = await storage.upload(storagePath, typedFile);
        if (uploadError) throw uploadError;
        let saved;
        try {
          saved = await recordCourseSource({ courseId, storagePath, originalName: file.name, sha256 });
        } catch (error) {
          await storage.remove([storagePath]);
          throw error;
        }
        if (!saved.source) {
          await storage.remove([storagePath]);
          throw new Error(saved.error);
        }
        updateSource(courseId, saved.source);
        setActivity((current) => ({
          ...current,
          [courseId]: saved.duplicate ? `${file.name} is already in this course.` : `Reading ${file.name}…`,
        }));
        if (!saved.duplicate) {
          void startProcessing(courseId, saved.source);
        }
      } catch (error) {
        console.error("Course source upload failed:", error);
        setActivity((current) => ({ ...current, [courseId]: errorMessage(error, `Couldn't add ${file.name}. Try again.`) }));
      }
    }
  }

  async function startProcessing(courseId: string, source: CourseSource) {
    setActivity((current) => ({ ...current, [courseId]: `Reading ${source.name}…` }));
    try {
      await apiRequest(`/api/documents/${source.id}/processing`, { method: "POST" });
      void pollSource(courseId, source);
    } catch (error) {
      setActivity((current) => ({ ...current, [courseId]: errorMessage(error, `Couldn't read ${source.name}. Try again.`) }));
    }
  }

  async function pollSource(courseId: string, source: CourseSource) {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      try {
        const status = await apiRequest<StatusReply>(`/api/documents/${source.id}/status`);
        const updated = { ...source, ...status };
        updateSource(courseId, updated);
        if (status.status === "ready" || status.status === "failed") {
          setActivity((current) => ({ ...current, [courseId]: status.status === "ready" ? `${source.name} is ready.` : status.error || `Couldn't read ${source.name}.` }));
          return;
        }
      } catch {
        return;
      }
    }
  }

  // Resume reading that was interrupted (the tab closed, or the server restarted).
  // The backend ignores a source it is still reading, so this never doubles work.
  useEffect(() => {
    for (const course of initialCourses) {
      for (const source of course.sources) {
        if (source.status === "pending" || source.status === "processing") void startProcessing(course.id, source);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, for the sources the page opened with
  }, []);

  async function openSource(source: CourseSource) {
    const result = await getCourseSourceUrl(source.id);
    if (result.url) window.open(result.url, "_blank", "noopener,noreferrer");
  }

  async function remove(courseId: string, source: CourseSource) {
    if (!window.confirm(`Remove “${source.name}” from this course?`)) return;
    const result = await removeCourseSource(source.id);
    if (result.error) {
      setActivity((current) => ({ ...current, [courseId]: result.error! }));
      return;
    }
    setCourses((current) => current.map((course) => course.id === courseId ? { ...course, sources: course.sources.filter((item) => item.id !== source.id) } : course));
  }

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      {courses.map((course) => {
        const titleId = `course-sources-${course.id}`;
        const descriptionId = `${titleId}-description`;
        return (
          <section key={course.id} className="overflow-hidden rounded-2xl border bg-card shadow-sm">
            <div className="border-b bg-gradient-to-br from-primary/[0.08] via-card to-card px-5 py-4">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 id={titleId} className="font-heading text-lg font-semibold">{course.code}</h2>
                  <p id={descriptionId} className="text-xs text-muted-foreground">{course.name || "Course materials"}</p>
                </div>
                <Badge variant="secondary">{course.sources.length} {course.sources.length === 1 ? "source" : "sources"}</Badge>
              </div>
            </div>
            <div className="space-y-4 p-5">
              <FileDropzone labelledBy={titleId} describedBy={descriptionId} onFiles={(files) => void addFiles(course.id, files)} />
              {activity[course.id] && <p className="text-xs text-muted-foreground" role="status">{activity[course.id]}</p>}
              {course.sources.length ? (
                <ul className="divide-y rounded-xl border">
                  {course.sources.map((source) => (
                    <li key={source.id} className="flex items-center gap-3 p-3">
                      <FileTypeIcon name={source.name} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium" title={source.name}>{source.title || source.name}</p>
                        <p className="truncate text-xs text-muted-foreground">
                          {source.title ? `${source.name} · ` : ""}{fileTypeLabel(source.name)}
                          {source.size !== null ? ` · ${formatFileSize(source.size)}` : ""}
                          {source.pageCount ? ` · ${source.pageCount} pages` : ""}
                        </p>
                        <SourceState source={source} />
                      </div>
                      <Button variant="ghost" size="icon-sm" aria-label={`Open ${source.name}`} onClick={() => void openSource(source)}>
                        <ExternalLink />
                      </Button>
                      {source.status === "failed" && (
                        <Button variant="ghost" size="icon-sm" aria-label={`Retry ${source.name}`} onClick={() => void startProcessing(course.id, source)}>
                          <RotateCcw />
                        </Button>
                      )}
                      <Button variant="ghost" size="icon-sm" aria-label={`Remove ${source.name}`} onClick={() => void remove(course.id, source)}>
                        <Trash2 />
                      </Button>
                    </li>
                  ))}
                </ul>
              ) : <p className="py-2 text-center text-xs text-muted-foreground">No materials saved for this course yet.</p>}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function SourceState({ source }: { source: CourseSource }) {
  if (source.status === "ready") return <span className="mt-1 inline-flex items-center gap-1 text-[11px] text-emerald-700 dark:text-emerald-400"><FileCheck2 className="size-3" /> Ready for AI</span>;
  if (source.status === "failed") return <span className="mt-1 inline-flex items-center gap-1 text-[11px] text-destructive"><TriangleAlert className="size-3" /> {source.error || "Could not read this file"}</span>;
  return <span className="mt-1 inline-flex items-center gap-1 text-[11px] text-muted-foreground"><LoaderCircle className="size-3 animate-spin" /> {source.status === "processing" ? "Reading and indexing" : "Waiting to be read"}</span>;
}

async function digestFile(file: File) {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
