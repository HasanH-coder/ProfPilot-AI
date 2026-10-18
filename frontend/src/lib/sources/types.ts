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
  /** The reusable lecture knowledge profile, in brief (the details come from the API). */
  knowledge: { state: KnowledgeState; topics: number | null };
};

export type KnowledgeState = "none" | "building" | "ready" | "outdated" | "failed";

export type CourseWithSources = {
  id: string;
  code: string;
  name: string | null;
  sources: CourseSource[];
};
