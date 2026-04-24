import { lazy, Suspense, useRef, useState } from "react";
import { Group, Panel, Separator } from "react-resizable-panels";
import { Button } from "./components/ui/button";
import { useAuth } from "./auth/useAuth";
import { ProjectList } from "./features/projects/ProjectList";
import { DocumentList } from "./features/documents/DocumentList";
import { ChatPane } from "./features/chat/ChatPane";
import type { ViewerSelection } from "./features/viewer/types";
import type { Citation } from "./api/types";

// `pdfjs-dist` alone is the majority of the production bundle (vite's build warned at ~780 kB
// for the main chunk before this) — the viewer is already conditionally rendered only once a
// citation is selected, so a dynamic import means nobody pays that cost on first load, only the
// first time they actually click a citation.
const ViewerPane = lazy(() =>
  import("./features/viewer/ViewerPane").then((m) => ({ default: m.ViewerPane })),
);

export function App() {
  const { user, isLoading, error, signIn, signOut } = useAuth();

  if (isLoading) {
    return <div className="p-4 text-lg">chat-with-docs-bedrock</div>;
  }

  if (user === null) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 p-4">
        <h1 className="text-lg font-medium">chat-with-docs-bedrock</h1>
        {error !== null && <p className="text-sm text-red-600">{error}</p>}
        <Button onClick={() => void signIn()}>Sign in</Button>
      </div>
    );
  }

  return <Workspace email={user.profile.email} onSignOut={signOut} />;
}

function Workspace({ email, onSignOut }: { email: string | undefined; onSignOut: () => void }) {
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [selection, setSelection] = useState<ViewerSelection | null>(null);
  // Increments on every citation click, including re-clicking the citation already selected, so
  // `HighlightOverlay`'s one-shot pulse restarts each time rather than only the first
  // (docs/06-frontend.md#pdf-viewer-and-highlighting).
  const nonceRef = useRef(0);

  // docs/06-frontend.md#rendering-citations: "Selects the citation's document in the viewer
  // pane ... scrolls to pageNumber ... draws the highlight rectangles and pulses them once."
  const handleCitationClick = (citation: Citation) => {
    nonceRef.current += 1;
    setSelection({
      documentId: citation.documentId,
      pageNumber: citation.pageNumber,
      rects: citation.rects,
      nonce: nonceRef.current,
    });
  };

  return (
    <div className="flex min-h-screen flex-col dark:bg-slate-950 dark:text-slate-100">
      <header className="flex items-center justify-between border-b border-slate-200 p-3 dark:border-slate-800">
        <h1 className="text-lg font-medium">chat-with-docs-bedrock</h1>
        <div className="flex items-center gap-3">
          <span className="text-sm text-slate-600 dark:text-slate-400">{email}</span>
          <Button variant="outline" onClick={onSignOut}>
            Sign out
          </Button>
        </div>
      </header>
      {selectedProjectId === null ? (
        <div className="flex flex-1">
          <div className="w-64 border-r border-slate-200 dark:border-slate-800">
            <ProjectList selectedProjectId={selectedProjectId} onSelect={setSelectedProjectId} />
          </div>
          <div className="flex flex-1 items-center justify-center text-sm text-slate-500">
            Select or create a project to see its documents.
          </div>
        </div>
      ) : (
        // docs/06-frontend.md#layout: "Three resizable panes. The viewer pane collapses when
        // nothing is selected, giving the chat the full width." The viewer `Panel` is only
        // mounted at all once something is selected, rather than rendered at zero width — this
        // also avoids loading a PDF/image nobody has asked to see yet.
        //
        // `defaultSize`/`minSize`/`maxSize` MUST be percentage strings ("16%"), not bare
        // numbers — react-resizable-panels treats a bare number as *pixels*, confirmed live
        // after deploy: every panel below rendered at single-digit-to-low-double-digit pixel
        // widths (16px/18px/etc.) with dragging capped at an equally tiny `maxSize` in px, i.e.
        // exactly the "collapsed to a narrow strip with no way to expand it" bug report.
        <Group orientation="horizontal" className="flex-1" id="workspace">
          <Panel id="projects" defaultSize="16%" minSize="12%" maxSize="30%">
            <ProjectList selectedProjectId={selectedProjectId} onSelect={setSelectedProjectId} />
          </Panel>
          <Separator className="w-1 cursor-col-resize bg-slate-200 hover:bg-slate-300 dark:bg-slate-800 dark:hover:bg-slate-700" />
          <Panel id="documents" defaultSize="18%" minSize="12%" maxSize="35%">
            <DocumentList projectId={selectedProjectId} />
          </Panel>
          <Separator className="w-1 cursor-col-resize bg-slate-200 hover:bg-slate-300 dark:bg-slate-800 dark:hover:bg-slate-700" />
          <Panel id="chat" defaultSize={selection !== null ? "36%" : "66%"} minSize="25%">
            <ChatPane projectId={selectedProjectId} onCitationClick={handleCitationClick} />
          </Panel>
          {selection !== null && (
            <>
              <Separator className="w-1 cursor-col-resize bg-slate-200 hover:bg-slate-300 dark:bg-slate-800 dark:hover:bg-slate-700" />
              <Panel id="viewer" defaultSize="30%" minSize="20%">
                <Suspense fallback={<p className="p-4 text-sm text-slate-500">Loading viewer…</p>}>
                  <ViewerPane
                    projectId={selectedProjectId}
                    selection={selection}
                    onClose={() => setSelection(null)}
                  />
                </Suspense>
              </Panel>
            </>
          )}
        </Group>
      )}
    </div>
  );
}
