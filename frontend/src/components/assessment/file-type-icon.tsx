import { FileIcon, FileImage, FileText, Presentation } from "lucide-react";

import { fileExtension } from "@/lib/documents/files";
import { cn } from "@/lib/utils";

/** A file's icon in a rounded square, red when the file has a problem. */
export function FileTypeIcon({ name, hasProblem = false }: { name: string; hasProblem?: boolean }) {
  const extension = fileExtension(name);
  const Icon =
    extension === "pptx"
      ? Presentation
      : ["png", "jpg", "jpeg", "webp"].includes(extension)
        ? FileImage
        : ["pdf", "docx", "txt"].includes(extension)
          ? FileText
          : FileIcon;

  return (
    <span
      className={cn(
        "flex size-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground",
        hasProblem && "bg-destructive/10 text-destructive",
      )}
    >
      <Icon className="size-4.5" />
    </span>
  );
}
