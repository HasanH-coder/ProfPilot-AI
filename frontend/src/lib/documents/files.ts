// What professors can upload. Shared by the upload UI (for clear, instant
// errors) and by the server action that records each upload (the check that
// counts). The Storage bucket enforces the same size and type limits.

export const ASSESSMENT_FILES_BUCKET = "assessment-files";

export const DOCUMENT_CATEGORIES = [
  "course_material",
  "previous_exam",
  "additional_attachment",
] as const;
export type DocumentCategory = (typeof DOCUMENT_CATEGORIES)[number];

/** An uploaded file, as recorded in the documents table. */
export type DocumentFile = {
  id: string;
  category: DocumentCategory;
  name: string;
  /** In bytes; null if unknown. */
  size: number | null;
};

export const MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024; // 25 MB

/** Allowed extensions, the content type each must have, and a readable label. */
const FILE_TYPES: Record<string, { mimeType: string; label: string }> = {
  pdf: { mimeType: "application/pdf", label: "PDF" },
  pptx: {
    mimeType: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    label: "PowerPoint",
  },
  docx: {
    mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    label: "Word",
  },
  txt: { mimeType: "text/plain", label: "Text" },
  png: { mimeType: "image/png", label: "PNG image" },
  jpg: { mimeType: "image/jpeg", label: "JPEG image" },
  jpeg: { mimeType: "image/jpeg", label: "JPEG image" },
  webp: { mimeType: "image/webp", label: "WebP image" },
};

/** For the file picker's accept attribute. */
export const ACCEPTED_EXTENSIONS = Object.keys(FILE_TYPES)
  .map((extension) => `.${extension}`)
  .join(",");
export const SUPPORTED_TYPES_TEXT = "PDF, PPTX, DOCX, TXT, PNG, JPG or WEBP";

export function isDocumentCategory(value: unknown): value is DocumentCategory {
  return DOCUMENT_CATEGORIES.includes(value as DocumentCategory);
}

export function fileExtension(name: string) {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

/** "PDF", "PowerPoint", … or the extension itself for unsupported files. */
export function fileTypeLabel(name: string) {
  const extension = fileExtension(name);
  return FILE_TYPES[extension]?.label ?? (extension ? extension.toUpperCase() : "File");
}

/**
 * Checks a file's extension, type and size. Returns the content type to store
 * it with, or an error message that explains why it can't be uploaded.
 */
export function checkFile(file: { name: string; type?: string | null; size: number }):
  | { contentType: string; error?: undefined }
  | { contentType?: undefined; error: string } {
  const extension = fileExtension(file.name);
  const allowed = FILE_TYPES[extension];
  if (!allowed) {
    return { error: `This file type isn't supported. Use ${SUPPORTED_TYPES_TEXT}.` };
  }

  // Browsers sometimes don't know a file's type; then the extension decides.
  const type = (file.type ?? "").split(";")[0].trim().toLowerCase();
  if (type && type !== "application/octet-stream" && type !== allowed.mimeType) {
    return { error: `This file's type (${type}) doesn't match its .${extension} extension.` };
  }

  if (file.size > MAX_FILE_SIZE_BYTES) {
    // Rounded up, so a file just over the limit never reads as "25 MB".
    const megabytes = Math.ceil((file.size / (1024 * 1024)) * 10) / 10;
    return { error: `This file is ${megabytes} MB. Files can be up to 25 MB.` };
  }

  return { contentType: allowed.mimeType };
}

/** A Storage-safe version of a file name. The original name is stored separately. */
export function safeFileName(name: string) {
  const extension = fileExtension(name);
  const base = (extension ? name.slice(0, -(extension.length + 1)) : name)
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "") // accents
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^[-.]+|[-.]+$/g, "")
    .slice(0, 80);
  return extension ? `${base || "file"}.${extension}` : base || "file";
}

/** For example "850 KB" or "2.4 MB". */
export function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace(/\.0$/, "")} MB`;
}
