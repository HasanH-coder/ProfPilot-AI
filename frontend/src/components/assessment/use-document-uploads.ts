"use client";

import { StorageApiError } from "@supabase/supabase-js";
import { useRef, useState } from "react";

import { createDraftForUploads } from "@/lib/assessments/actions";
import { recordUploadedDocument, removeDocument } from "@/lib/documents/actions";
import {
  ASSESSMENT_FILES_BUCKET,
  checkFile,
  safeFileName,
  SUPPORTED_TYPES_TEXT,
  type DocumentCategory,
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

/** A file the professor added on this page. */
export type UploadItem = {
  /** Tells items apart on this page. Not stored anywhere. */
  key: string;
  category: DocumentCategory;
  file: File;
  status: UploadStatus;
  /** The file's row in the documents table, once Supabase confirms the upload. */
  documentId?: string;
  /** Why the file wasn't added, didn't upload, or couldn't be removed. */
  error?: string;
};

export type DocumentUploads = ReturnType<typeof useDocumentUploads>;

const UPLOAD_FAILED = "The upload failed. Check your connection and try again.";
const REMOVE_FAILED = "The file couldn't be removed. Please try again.";

/**
 * Uploads the professor's files from the browser straight to Supabase Storage,
 * then has the server record each one. Files belong to an assessment draft,
 * which is created from draftDetails when the first file is added.
 */
export function useDocumentUploads(draftDetails: { courseId: string | null; examName: string }) {
  const [items, setItems] = useState<UploadItem[]>([]);
  // Every upload, and saving the form, reuses the one draft this request creates.
  const draftRequest = useRef<ReturnType<typeof createDraftForUploads> | null>(null);

  async function getDraft() {
    draftRequest.current ??= createDraftForUploads(draftDetails);
    try {
      const result = await draftRequest.current;
      // If the draft couldn't be created, the next upload tries again.
      if (!result.draft) draftRequest.current = null;
      return result;
    } catch (error) {
      draftRequest.current = null;
      throw error;
    }
  }

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
        file,
        status: error ? "rejected" : "uploading",
        error,
      };
    });
    setItems((current) => [...current, ...added]);
    for (const item of added) {
      if (item.status === "uploading") void upload(item.key, category, item.file);
    }
  }

  function retry(item: UploadItem) {
    void upload(item.key, item.category, item.file);
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

  /** The draft's id once a file has been added, so saving updates it instead of adding another. */
  async function getExamProjectId() {
    const result = await draftRequest.current?.catch(() => null);
    return result?.draft?.examProjectId ?? null;
  }

  return {
    items,
    /** Files are still uploading or being removed. */
    isBusy: items.some((item) => item.status === "uploading" || item.status === "removing"),
    addFiles,
    retry,
    remove,
    getExamProjectId,
  };
}

/** Uploads one file and records it. Returns the new document's id, or why it failed. */
async function uploadFile(
  category: DocumentCategory,
  file: File,
  getDraft: () => ReturnType<typeof createDraftForUploads>,
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
