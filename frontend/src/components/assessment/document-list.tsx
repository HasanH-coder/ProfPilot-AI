import { FileTypeIcon } from "@/components/assessment/file-type-icon";
import { fileTypeLabel, formatFileSize, type DocumentFile } from "@/lib/documents/files";

/** Uploaded files, read-only: each with its type and size. */
export function DocumentList({ files }: { files: DocumentFile[] }) {
  return (
    <ul className="flex flex-col gap-2">
      {files.map((file) => (
        <li key={file.id} className="flex items-center gap-3 rounded-lg border bg-card p-3">
          <FileTypeIcon name={file.name} />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium" title={file.name}>
              {file.name}
            </p>
            <p className="text-xs text-muted-foreground">
              {[fileTypeLabel(file.name), file.size !== null && formatFileSize(file.size)]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
        </li>
      ))}
    </ul>
  );
}
