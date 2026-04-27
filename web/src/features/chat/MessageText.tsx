import { Fragment, type KeyboardEvent } from "react";
import { splitTextByCitations } from "../../lib/citations";
import { cn } from "../../lib/utils";
import type { Citation } from "../../api/types";

/**
 * Arrow keys move between citations in the focused message" - a citation button's
 * `ArrowLeft`/`ArrowRight` moves focus to the previous/ next citation button within the same
 * message, wrapping at the ends.
 */
function handleCitationKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
  if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
  const container = event.currentTarget.closest("[data-message-citations]");
  if (container === null) return;
  const buttons = Array.from(
    container.querySelectorAll<HTMLButtonElement>("[data-citation-button]"),
  );
  const index = buttons.indexOf(event.currentTarget);
  if (index === -1 || buttons.length < 2) return;
  const delta = event.key === "ArrowRight" ? 1 : -1;
  const next = buttons[(index + delta + buttons.length) % buttons.length];
  next?.focus();
  event.preventDefault();
}

/**
 * Cited runs get a subtle underline and a superscript `[n]` marker per citation, a real `<button>`
 * so it's keyboard-reachable. Hovering shows `citedText` via the native `title` attribute;
 * `aria-describedby` additionally points at a visually-hidden description with the same text.
 * Clicking selects the document in the viewer pane, scrolls to the page, and pulses the highlight -
 * see `App.tsx`'s `Workspace` for where `onCitationClick` ultimately leads.
 */
export function MessageText({
  text,
  citations = [],
  onCitationClick,
}: {
  text: string;
  citations?: Citation[];
  onCitationClick?: (citation: Citation) => void;
}) {
  const runs = splitTextByCitations(text, citations);

  return (
    <span data-message-citations>
      {runs.map((run, index) => (
        <Fragment key={index}>
          <span className={cn(run.citations.length > 0 && "underline decoration-dotted")}>
            {run.text}
          </span>
          {run.citations.map((citation) => (
            <sup key={citation.citationId}>
              <button
                type="button"
                data-citation-button
                title={citation.citedText}
                aria-describedby={`citation-${citation.citationId}-desc`}
                onClick={() => onCitationClick?.(citation)}
                onKeyDown={handleCitationKeyDown}
                // `leading-none` overrides `line-height: 0`, which Tailwind Preflight sets on
                // `sub`/`sup` so a superscript doesn't stretch the surrounding text line's
                // height — fine for plain text (glyph ink still paints outside a zero-height
                // line box) but fatal for this `inline-block` button: with no explicit
                // line-height of its own, an inline-block's own box height *is* its inherited
                // line-height.
                className="ml-0.5 cursor-pointer text-[0.7em] leading-none text-blue-600 hover:underline dark:text-blue-400"
              >
                [{citation.citationId.replace(/^c/, "")}]
              </button>
              <span id={`citation-${citation.citationId}-desc`} className="sr-only">
                {citation.citedText}
              </span>
            </sup>
          ))}
        </Fragment>
      ))}
    </span>
  );
}
