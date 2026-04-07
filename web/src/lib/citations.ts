import type { Citation } from "../api/types";

/**
 * docs/06-frontend.md#rendering-citations: "walking `citations` sorted by `spanStart` and
 * splitting the text into runs." Pure boundary-overlay algorithm — every distinct
 * `spanStart`/`spanEnd` becomes a cut point, and each resulting run carries every citation whose
 * span fully covers it (usually zero or one, but citations may share a span when one sentence
 * draws from multiple sources, per docs/04-retrieval-and-citations.md's streaming table).
 */
export interface CitationRun {
  text: string;
  citations: Citation[];
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

export function splitTextByCitations(text: string, citations: Citation[]): CitationRun[] {
  if (citations.length === 0) {
    return text.length > 0 ? [{ text, citations: [] }] : [];
  }

  const boundaries = new Set<number>([0, text.length]);
  for (const citation of citations) {
    boundaries.add(clamp(citation.spanStart, 0, text.length));
    boundaries.add(clamp(citation.spanEnd, 0, text.length));
  }
  const sorted = [...boundaries].sort((a, b) => a - b);

  const runs: CitationRun[] = [];
  for (let i = 0; i < sorted.length - 1; i++) {
    const start = sorted[i];
    const end = sorted[i + 1];
    if (start === undefined || end === undefined || start === end) continue;
    const covering = citations.filter((c) => c.spanStart <= start && c.spanEnd >= end);
    runs.push({ text: text.slice(start, end), citations: covering });
  }
  return runs;
}
