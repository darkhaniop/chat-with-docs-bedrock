import type { Rect } from "../../api/types";

/**
 * The viewer pane's selection state, lifted to `App.tsx`'s `Workspace` (docs/06-frontend.md's
 * three-pane layout — the viewer needs to react to a citation clicked inside the chat pane, so
 * neither owns the state alone). `nonce` increments on every citation click, including
 * re-clicking the citation already selected, so `HighlightOverlay`'s one-shot pulse animation
 * restarts each time rather than only the first.
 */
export interface ViewerSelection {
  documentId: string;
  pageNumber: number;
  rects: Rect[];
  nonce: number;
}
