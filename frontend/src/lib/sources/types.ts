import type { DocumentFile } from "@/lib/documents/files";

export type SourceStatus = "pending" | "processing" | "ready" | "failed";

/** A reusable, course-owned document. It is never owned by one assessment. */
export type CourseSource = DocumentFile & {
  courseId: string;
  status: SourceStatus;
  error: string | null;
  pageCount: number | null;
  title: string | null;
  createdAt: string;
};

export type CourseWithSources = {
  id: string;
  code: string;
  name: string | null;
  sources: CourseSource[];
};
