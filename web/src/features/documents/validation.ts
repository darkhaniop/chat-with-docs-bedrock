/** Mirrors services/api/api/validation.py's accepted types and services/common/common/config.py's
 * `max_document_bytes` — client-side only to give an immediate error instead of a wasted round
 * trip (docs/06-frontend.md#upload-flow); the API re-validates everything server-side. */
export const ACCEPTED_CONTENT_TYPES = [
  "application/pdf",
  "image/png",
  "image/jpeg",
  "image/webp",
] as const;

export const MAX_DOCUMENT_BYTES = 200 * 1024 * 1024;

export function validateFile(file: File): string | null {
  if (!ACCEPTED_CONTENT_TYPES.includes(file.type as (typeof ACCEPTED_CONTENT_TYPES)[number])) {
    return `${file.type || "unknown type"} is not supported. Upload a PDF, PNG, JPEG, or WebP.`;
  }
  if (file.size > MAX_DOCUMENT_BYTES) {
    const limitMb = Math.round(MAX_DOCUMENT_BYTES / (1024 * 1024));
    return `File is too large (limit ${limitMb} MB).`;
  }
  return null;
}
