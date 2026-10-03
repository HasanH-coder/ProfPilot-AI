"use client";

import { StorageApiError } from "@supabase/supabase-js";
import { useState } from "react";

import type { CreateDraftResult } from "@/lib/assessments/actions";
import { recordUploadedDocument, removeDocument } from "@/lib/documents/actions";
import {
  ASSESSMENT_FILES_BUCKET,
  checkFile,
  safeFileName,
  SUPPORTED_TYPES_TEXT,
  type DocumentCategory,
  type DocumentFile,
} from "@/lib/documents/files";
import { createClient } from "@/lib/supabase/client";

/**
 * rejected:  not uploaded, because the file isn't allowed
 * uploading: being uploaded and recorded
 * uploaded:  saved in Storage and recorded in the documents table
 * failed:    the upload didn't work; it can be retried
 * removing:  being deleted
 */
export type UploadStatus = "rejected" | "uploading" | "uploaded" | "failed" | "removing";

/** A file in one of the form's upload sections. */
export type UploadItem = {
  /** Tells items apart on this page. Not stored anywhere. */
  key: string;
  category: DocumentCategory;
  name: string;
  /** In bytes; null if unknown. */
  size: number | null;
  status: UploadStatus;
  /** The file's row in the documents table, once Supabase confirms the upload. */
  documentId?: string;
  /** Why the file wasn't added, didn't upload, or couldn't be removed. */
  error?: string;
  /** The file chosen on this page, kept so a failed upload can be retried. */
  file?: File;
};

export type DocumentUploads = ReturnType<typeof useDocumentUploads>;

const UPLOAD_FAILED = "The upload failed. Check your connection and try again.";
const REMOVE_FAILED = "The file couldn't be removed. Please try again.";

/**
 * Lists the draft's files and uploads new ones from the browser straight to
 * Supabase Storage, then has the server record each one. savedFiles are the
 * files the draft already has. getDraft gives the draft new files belong to,
 * creating it if this is a new assessment.
 */
export function useDocumentUploads(
  savedFiles: DocumentFile[],
  getDraft: () => Promise<CreateDraftResult>,
) {
  const [items, setItems] = useState<UploadItem[]>(() =>
    savedFiles.map((file) => ({
      key: file.id,
      category: file.category,
      name: file.name,
      size: file.size,
      status: "uploaded",
      documentId: file.id,
    })),
  );

  function updateItem(key: string, changes: Partial<UploadItem>) {
    setItems((current) =>
      current.map((item) => (item.key === key ? { ...item, ...changes } : item)),
    );
  }

  function forgetItem(key: string) {
    setItems((current) => current.filter((item) => item.key !== key));
  }

  async function upload(key: string, category: DocumentCategory, file: File) {
    updateItem(key, { status: "uploading", error: undefined });
    try {
      const { documentId, error } = await uploadFile(category, file, getDraft);
      if (documentId) updateItem(key, { status: "uploaded", documentId });
      else updateItem(key, { status: "failed", error: error ?? UPLOAD_FAILED });
    } catch (error) {
      console.error("Upload failed:", error);
      updateItem(key, { status: "failed", error: UPLOAD_FAILED });
    }
  }

  /** Adds files to a category. Files that aren't allowed are listed with the reason. */
  function addFiles(category: DocumentCategory, files: File[]) {
    const added = files.map((file): UploadItem => {
      const { error } = checkFile(file);
      return {
        key: crypto.randomUUID(),
        category,
        name: file.name,
        size: file.size,
        status: error ? "rejected" : "uploading",
        error,
        file,
      };
    });
    setItems((current) => [...current, ...added]);
    for (const item of added) {
      if (item.status === "uploading" && item.file) void upload(item.key, category, item.file);
    }
  }

  function retry(item: UploadItem) {
    if (item.file) void upload(item.key, item.category, item.file);
  }

  /** Deletes an uploaded file, or clears a file that was never saved. */
  async function remove(item: UploadItem) {
    if (!item.documentId) {
      forgetItem(item.key);
      return;
    }

    updateItem(item.key, { status: "removing", error: undefined });
    let error: string | undefined;
    try {
      ({ error } = await removeDocument(item.documentId));
    } catch (cause) {
      console.error("Remove failed:", cause);
      error = REMOVE_FAILED;
    }
    if (error) updateItem(item.key, { status: "uploaded", error });
    else forgetItem(item.key);
  }

  return {
    items,
    /** Files are still uploading or being removed. */
    isBusy: items.some((item) => item.status === "uploading" || item.status === "removing"),
    addFiles,
    retry,
    remove,
  };
}

/** Uploads one file and records it. Returns the new document's id, or why it failed. */
async function uploadFile(
  category: DocumentCategory,
  file: File,
  getDraft: () => Promise<CreateDraftResult>,
): Promise<{ documentId?: string; error?: string }> {
  const { contentType, error: fileError } = checkFile(file);
  if (!contentType) return { error: fileError };
  const { draft, error: draftError } = await getDraft();
  if (!draft) return { error: draftError };

  const storage = createClient().storage.from(ASSESSMENT_FILES_BUCKET);
  const storagePath = `${draft.folder}/${category}/${crypto.randomUUID()}-${safeFileName(file.name)}`;
  // Storage keeps the type the file is sent with. Browsers don't always know a
  // file's type, so send it with the type that matches its extension.
  const typedFile = new File([file], file.name, { type: contentType });
  const { error: uploadError } = await storage.upload(storagePath, typedFile);
  if (uploadError) return { error: uploadErrorMessage(uploadError) };

  try {
    // The server checks the upload and records it, or deletes it if it can't.
    return await recordUploadedDocument({
      examProjectId: draft.examProjectId,
      category,
      storagePath,
      originalName: file.name,
    });
  } catch (error) {
    // The server wasn't reached, so delete the uploaded file from here instead.
    await storage.remove([storagePath]);
    throw error;
  }
}

/** The bucket also enforces the size and type limits, in case the browser's check is skipped. */
function uploadErrorMessage(error: Error) {
  const code = error instanceof StorageApiError ? error.code : undefined;
  if (code === "EntityTooLarge") return "This file is larger than 25 MB.";
  if (code === "InvalidMimeType") return `This file type isn't supported. Use ${SUPPORTED_TYPES_TEXT}.`;
  return UPLOAD_FAILED;
}
