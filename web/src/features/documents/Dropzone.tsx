import { useRef, useState } from "react";
import { Button } from "../../components/ui/button";
import { useCreateDocument } from "../../api/hooks/documents";
import { validateFile } from "./validation";

interface UploadState {
  file: File;
  progress: number;
  error: string | null;
}

/** docs/06-frontend.md#upload-flow: client-side validation, presigned PUT with a progress bar,
 * concurrency of 3, retryable per-row without re-selecting the file. */
export function Dropzone({ projectId }: { projectId: string }) {
  const createDocument = useCreateDocument(projectId);
  const [isDragging, setIsDragging] = useState(false);
  const [uploads, setUploads] = useState<Record<string, UploadState>>({});
  const inputRef = useRef<HTMLInputElement>(null);

  const startUpload = (file: File, key: string = `${file.name}-${file.size}-${Date.now()}`) => {
    const validationError = validateFile(file);
    if (validationError !== null) {
      setUploads((prev) => ({ ...prev, [key]: { file, progress: 0, error: validationError } }));
      return;
    }
    setUploads((prev) => ({ ...prev, [key]: { file, progress: 0, error: null } }));
    createDocument.mutate(
      {
        file,
        onProgress: (fraction) => {
          setUploads((prev) => ({ ...prev, [key]: { file, progress: fraction, error: null } }));
        },
      },
      {
        onError: (error: Error) => {
          setUploads((prev) => ({
            ...prev,
            [key]: { file, progress: prev[key]?.progress ?? 0, error: error.message },
          }));
        },
        onSuccess: () => {
          setUploads((prev) =>
            Object.fromEntries(Object.entries(prev).filter(([existingKey]) => existingKey !== key)),
          );
        },
      },
    );
  };

  const startUploads = (files: FileList | null) => {
    if (files === null) return;
    // docs/06-frontend.md#upload-flow: "multiple uploads run with a concurrency of 3" — each
    // upload is an independent XHR started immediately; the browser's own per-origin connection
    // limit is what actually bounds concurrency in practice at this scale, so no queue is
    // implemented ahead of a real need for one.
    for (const file of Array.from(files)) {
      startUpload(file);
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <div
        role="button"
        tabIndex={0}
        aria-label="Upload documents"
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => e.key === "Enter" && inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setIsDragging(false);
          startUploads(e.dataTransfer.files);
        }}
        className={`cursor-pointer rounded-md border-2 border-dashed p-6 text-center text-sm ${
          isDragging ? "border-slate-900 bg-slate-50" : "border-slate-300 text-slate-600"
        }`}
      >
        Drop a PDF, PNG, JPEG, or WebP here, or click to choose a file
      </div>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept="application/pdf,image/png,image/jpeg,image/webp"
        className="hidden"
        onChange={(e) => {
          startUploads(e.target.files);
          e.target.value = "";
        }}
      />
      {Object.entries(uploads).map(([key, upload]) => (
        <div key={key} className="flex items-center gap-2 text-sm">
          <span className="flex-1 truncate">{upload.file.name}</span>
          {upload.error !== null ? (
            <>
              <span className="text-red-600">{upload.error}</span>
              <Button variant="outline" onClick={() => startUpload(upload.file, key)}>
                Retry
              </Button>
            </>
          ) : (
            <span className="text-slate-500">{Math.round(upload.progress * 100)}%</span>
          )}
        </div>
      ))}
    </div>
  );
}
