"use client";

import { useEffect, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { ASSESSMENT_FILES_BUCKET } from "@/lib/documents/files";
import { createClient } from "@/lib/supabase/client";

/**
 * An uploaded image shown in a question. The bucket is private: the image is
 * loaded through a short-lived signed link, made with the professor's session.
 */
export function FigurePreview({ documentId }: { documentId: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [name, setName] = useState("Figure");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const supabase = createClient();
      // Row Level Security only returns the professor's own file.
      const { data: document } = await supabase
        .from("documents")
        .select("storage_path, original_name")
        .eq("id", documentId)
        .maybeSingle();
      if (!document) {
        if (!cancelled) setFailed(true);
        return;
      }
      const { data } = await supabase.storage
        .from(ASSESSMENT_FILES_BUCKET)
        .createSignedUrl(document.storage_path, 10 * 60);
      if (cancelled) return;
      if (data?.signedUrl) {
        setUrl(data.signedUrl);
        setName(document.original_name);
      } else {
        setFailed(true);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  if (failed) return <p className="text-xs text-muted-foreground">The figure for this question couldn&apos;t be loaded.</p>;
  if (!url) return <Skeleton className="h-40 w-full max-w-md rounded-lg" />;
  return (
    // A signed, expiring link to a private file: not something next/image should cache.
    // eslint-disable-next-line @next/next/no-img-element
    <img src={url} alt={`Figure: ${name}`} className="max-h-80 w-auto max-w-full rounded-lg border object-contain" />
  );
}
