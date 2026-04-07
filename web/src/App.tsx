import { useState } from "react";
import { Button } from "./components/ui/button";
import { useAuth } from "./auth/useAuth";
import { ProjectList } from "./features/projects/ProjectList";
import { DocumentList } from "./features/documents/DocumentList";
import { ChatPane } from "./features/chat/ChatPane";

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

  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between border-b border-slate-200 p-3">
        <h1 className="text-lg font-medium">chat-with-docs-bedrock</h1>
        <div className="flex items-center gap-3">
          <span className="text-sm text-slate-600">{email}</span>
          <Button variant="outline" onClick={onSignOut}>
            Sign out
          </Button>
        </div>
      </header>
      <div className="flex flex-1">
        <ProjectList selectedProjectId={selectedProjectId} onSelect={setSelectedProjectId} />
        {selectedProjectId !== null ? (
          <>
            <div className="w-80 border-r border-slate-200">
              <DocumentList projectId={selectedProjectId} />
            </div>
            <ChatPane projectId={selectedProjectId} />
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-slate-500">
            Select or create a project to see its documents.
          </div>
        )}
      </div>
    </div>
  );
}
