import { Fragment } from "react";
import { splitTextByCitations } from "../../lib/citations";
import { cn } from "../../lib/utils";
import type { Citation } from "../../api/types";

/**
 * Cited runs get a subtle underline and a superscript `[n]` marker per citation, a real `<button>`
 * so it's keyboard-reachable. Hovering shows `citedText`.
 */
export function MessageText({ text, citations = [] }: { text: string; citations?: Citation[] }) {
  const runs = splitTextByCitations(text, citations);

  return (
    <span>
      {runs.map((run, index) => (
        <Fragment key={index}>
          <span className={cn(run.citations.length > 0 && "underline decoration-dotted")}>
            {run.text}
          </span>
          {run.citations.map((citation) => (
            <sup key={citation.citationId}>
              <button
                type="button"
                title={citation.citedText}
                className="ml-0.5 cursor-default text-[0.7em] text-blue-600"
              >
                [{citation.citationId.replace(/^c/, "")}]
              </button>
            </sup>
          ))}
        </Fragment>
      ))}
    </span>
  );
}
