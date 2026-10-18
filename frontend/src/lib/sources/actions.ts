"use server";

import { refresh } from "next/cache";

import { getCurrentProfessor } from "@/lib/auth/current-professor";
import { ASSESSMENT_FILES_BUCKET, checkFile } from "@/lib/documents/files";
import { SOURCE_COLUMNS, toCourseSource } from "@/lib/sources/queries";
import type { CourseSource } from "@/lib/sources/types";
import { createClient } from "@/lib/supabase/server";

const SHA256 = /^[a-f0-9]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const REMOVE_FAILED = "The source couldn't be removed. Please try again.";

export type RecordCourseSourceInput = {
  courseId: string;
  storagePath: string;
  originalName: string;
  sha256: string;
};

export async function findCourseSource(courseId: string, sha256: string): Promise<{ source?: CourseSource }> {
  await getCurrentProfessor();
  if (!SHA256.test(sha256)) return {};
  const supabase = await createClient();
  const { data } = await supabase
    .from("documents")
    .select(SOURCE_COLUMNS)
    .eq("course_id", courseId)
    .is("exam_project_id", null)
    .eq("category", "course_material")
    .eq("content_sha256", sha256)
    .eq("deletion_pending", false)
    .maybeSingle();
  return data ? { source: toCourseSource(data) } : {};
}

export async function recordCourseSource(
  input: RecordCourseSourceInput,
): Promise<{ source?: CourseSource; duplicate?: boolean; error?: string }> {
  const professor = await getCurrentProfessor();
  const supabase = await createClient();
  const storage = supabase.storage.from(ASSESSMENT_FILES_BUCKET);
  const { courseId, storagePath, originalName, sha256 } = input ?? {};
  const folder = `${professor.id}/${courseId}/sources/`;
  if (
    typeof courseId !== "string" ||
    typeof storagePath !== "string" ||
    typeof originalName !== "string" ||
    !SHA256.test(sha256) ||
    !storagePath.startsWith(folder) ||
    storagePath.slice(folder.length).includes("/")
  ) {
    return { error: "This source couldn't be saved. Please try again." };
  }

  const [{ data: course }, { data: stored }] = await Promise.all([
    supabase.from("courses").select("id").eq("id", courseId).maybeSingle(),
    storage.info(storagePath),
  ]);
  if (!course) {
    await storage.remove([storagePath]);
    return { error: "This course no longer exists. Please reload the page." };
  }
  if (!stored) return { error: "The upload didn't finish. Please try again." };
  const problem = checkFile({ name: originalName, type: stored.contentType, size: stored.size ?? 0 });
  if (problem.error) {
    await storage.remove([storagePath]);
    return { error: problem.error };
  }

  // The browser checked for a duplicate just before uploading (findCourseSource). One saved
  // since then is caught by the unique index on the digest, and handled below.
  const { data, error } = await supabase
    .from("documents")
    .insert({
      course_id: courseId,
      exam_project_id: null,
      category: "course_material",
      original_name: originalName.slice(0, 255),
      storage_path: storagePath,
      mime_type: stored.contentType ?? null,
      size_bytes: stored.size ?? null,
      content_sha256: sha256,
    })
    .select(SOURCE_COLUMNS)
    .single();

  if (error || !data) {
    const raced = await findCourseSource(courseId, sha256);
    await storage.remove([storagePath]);
    if (raced.source) return { source: raced.source, duplicate: true };
    console.error("Could not record course source:", error);
    return { error: "The source couldn't be saved. Please try again." };
  }
  refresh();
  return { source: toCourseSource(data) };
}

export async function removeCourseSource(documentId: string): Promise<{ error?: string }> {
  await getCurrentProfessor();
  const supabase = await createClient();
  // Reserve the source first. The database refuses while an assessment has it
  // selected, and Storage only deletes the file of a reserved source.
  const { data: document, error: reserveError } = await supabase
    .from("documents")
    .update({ deletion_pending: true })
    .eq("id", documentId)
    .is("exam_project_id", null)
    .eq("category", "course_material")
    .select("id, storage_path")
    .maybeSingle();
  if (reserveError) {
    if (reserveError.code === "23503") return { error: "An assessment uses this source. Unselect it there first." };
    console.error("Could not reserve course source for removal:", reserveError);
    return { error: REMOVE_FAILED };
  }
  if (!document) return {};

  const { error: storageError } = await supabase.storage
    .from(ASSESSMENT_FILES_BUCKET)
    .remove([document.storage_path]);
  if (storageError) {
    // The file is still there, so the source stays usable.
    await supabase.from("documents").update({ deletion_pending: false }).eq("id", document.id);
    return { error: REMOVE_FAILED };
  }
  const { error } = await supabase.from("documents").delete().eq("id", document.id);
  if (error) return { error: REMOVE_FAILED };
  refresh();
  return {};
}

export async function getCourseSourceUrl(documentId: string): Promise<{ url?: string; error?: string }> {
  await getCurrentProfessor();
  const supabase = await createClient();
  const { data: document } = await supabase
    .from("documents")
    .select("storage_path")
    .eq("id", documentId)
    .is("exam_project_id", null)
    .eq("category", "course_material")
    .maybeSingle();
  if (!document) return { error: "This source no longer exists." };
  const { data, error } = await supabase.storage
    .from(ASSESSMENT_FILES_BUCKET)
    .createSignedUrl(document.storage_path, 60);
  return error || !data ? { error: "The source couldn't be opened." } : { url: data.signedUrl };
}

/**
 * Replaces the course sources an assessment uses, atomically. Only the
 * professor's own sources from the assessment's saved course are kept: an id
 * the form still holds for a source that was removed, or from another course,
 * is left out rather than failing the save. The database checks the same again.
 */
export async function setAssessmentSourceDocuments(
  assessmentId: string,
  documentIds: string[],
): Promise<{ error?: string }> {
  await getCurrentProfessor();
  const supabase = await createClient();
  const requested = Array.isArray(documentIds) ? [...new Set(documentIds)].filter((id) => UUID.test(id)) : [];
  let kept: string[] = [];
  // Row Level Security returns only the professor's own rows.
  const { data: assessment } =
    requested.length > 0
      ? await supabase.from("exam_projects").select("course_id").eq("id", assessmentId).maybeSingle()
      : { data: null };
  if (assessment?.course_id) {
    const { data: selectable, error: lookupError } = await supabase
      .from("documents")
      .select("id")
      .in("id", requested)
      .eq("course_id", assessment.course_id)
      .is("exam_project_id", null)
      .eq("category", "course_material")
      .eq("deletion_pending", false);
    if (lookupError) {
      console.error("Could not check the assessment's sources:", lookupError);
      return { error: "The selected sources couldn't be saved. Please try again." };
    }
    kept = requested.filter((id) => selectable.some((row) => row.id === id));
  }
  const { error } = await supabase.rpc("set_assessment_source_documents", {
    p_exam_project_id: assessmentId,
    p_document_ids: kept,
  });
  if (error) {
    console.error("Could not save the assessment's sources:", error);
    return { error: "The selected sources couldn't be saved. Please try again." };
  }
  refresh();
  return {};
}
