"use server";

import { refresh } from "next/cache";

import { getCurrentProfessor } from "@/lib/auth/current-professor";
import {
  ASSESSMENT_FILES_BUCKET,
  checkFile,
  isDocumentCategory,
  type DocumentCategory,
} from "@/lib/documents/files";
import { createClient } from "@/lib/supabase/server";

export type RecordDocumentInput = {
  examProjectId: string;
  category: DocumentCategory;
  /** Where the browser uploaded the file in the assessment-files bucket. */
  storagePath: string;
  /** The file name as the professor knows it. */
  originalName: string;
};

/**
 * Records a file the browser has just uploaded to Storage. Nothing from the
 * browser is taken on trust: the path must be in the professor's own folder,
 * the size and type are read back from Storage, and the course comes from the
 * assessment draft. If the record can't be saved, the uploaded file is deleted
 * so no file is left behind without a record.
 */
export async function recordUploadedDocument(
  input: RecordDocumentInput,
): Promise<{ documentId?: string; error?: string }> {
  const professor = await getCurrentProfessor();
  const supabase = await createClient();
  const storage = supabase.storage.from(ASSESSMENT_FILES_BUCKET);

  const { examProjectId, category, storagePath, originalName } = input ?? {};
  const folder = `${professor.id}/${examProjectId}/${category}/`;
  if (
    typeof examProjectId !== "string" ||
    typeof storagePath !== "string" ||
    typeof originalName !== "string" ||
    !isDocumentCategory(category) ||
    !storagePath.startsWith(folder) ||
    storagePath.slice(folder.length).includes("/")
  ) {
    return { error: "This upload couldn't be saved. Please try again." };
  }

  // What Storage actually received: size and content type.
  const { data: stored } = await storage.info(storagePath);
  if (!stored) return { error: "The upload didn't finish. Please try again." };

  const problem = checkFile({ name: originalName, type: stored.contentType, size: stored.size ?? 0 });
  if (problem.error) {
    await storage.remove([storagePath]);
    return { error: problem.error };
  }

  // Row Level Security only returns the draft if it belongs to this professor.
  const { data: examProject } = await supabase
    .from("exam_projects")
    .select("id, course_id")
    .eq("id", examProjectId)
    .maybeSingle();
  if (!examProject) {
    await storage.remove([storagePath]);
    return { error: "This assessment draft no longer exists. Please reload the page." };
  }

  const { data: document, error } = await supabase
    .from("documents")
    .insert({
      exam_project_id: examProject.id,
      course_id: examProject.course_id,
      category,
      original_name: originalName.slice(0, 255),
      storage_path: storagePath,
      mime_type: stored.contentType ?? null,
      size_bytes: stored.size ?? null,
    })
    .select("id")
    .single();

  if (error) {
    console.error("Could not record uploaded document:", error);
    await storage.remove([storagePath]);
    return { error: "The file couldn't be saved. Please try again." };
  }

  // Pages that list the assessment's files, including ones in the browser's history, show it.
  refresh();
  return { documentId: document.id };
}

/**
 * Deletes a document: first the file in Storage, then its record. If the
 * record can't be deleted, trying again is safe because removing a file that
 * is already gone succeeds.
 */
export async function removeDocument(documentId: string): Promise<{ error?: string }> {
  await getCurrentProfessor();
  const supabase = await createClient();

  // Row Level Security only finds the document if it belongs to this professor.
  const { data: document } = await supabase
    .from("documents")
    .select("id, storage_path")
    .eq("id", documentId)
    .maybeSingle();
  if (!document) return {};

  const { error: storageError } = await supabase.storage
    .from(ASSESSMENT_FILES_BUCKET)
    .remove([document.storage_path]);
  if (storageError) {
    console.error("Could not delete file from Storage:", storageError);
    return { error: "The file couldn't be removed. Please try again." };
  }

  const { error } = await supabase.from("documents").delete().eq("id", document.id);
  if (error) {
    console.error("Could not delete document record:", error);
    return { error: "The file couldn't be removed. Please try again." };
  }

  refresh();
  return {};
}
