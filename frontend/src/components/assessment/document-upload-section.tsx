"use client";

import { useId } from "react";

import { FileDropzone } from "@/components/assessment/file-dropzone";
import { FormSection } from "@/components/assessment/form-section";
import { UploadList } from "@/components/assessment/upload-list";
import type { DocumentUploads } from "@/components/assessment/use-document-uploads";
import type { DocumentCategory } from "@/lib/documents/files";

type DocumentUploadSectionProps = {
  title: string;
  description: string;
  /** Which kind of files this section holds. Each section keeps its own category. */
  category: DocumentCategory;
  uploads: DocumentUploads;
};

/** A section of the Create assessment form where the professor adds one kind of file. */
export function DocumentUploadSection({
  title,
  description,
  category,
  uploads,
}: DocumentUploadSectionProps) {
  const titleId = useId();
  const descriptionId = useId();
  const items = uploads.items.filter((item) => item.category === category);

  return (
    <FormSection
      title={title}
      titleId={titleId}
      description={description}
      descriptionId={descriptionId}
    >
      <div className="flex flex-col gap-3">
        <FileDropzone
          labelledBy={titleId}
          describedBy={descriptionId}
          onFiles={(files) => uploads.addFiles(category, files)}
        />
        {items.length > 0 && (
          <UploadList items={items} onRetry={uploads.retry} onRemove={uploads.remove} />
        )}
      </div>
    </FormSection>
  );
}
