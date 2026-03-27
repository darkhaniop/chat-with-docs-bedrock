import { useState } from "react";
import { apiFetch } from "./api/client";
import { useAuth } from "./auth/useAuth";
import { Button } from "./components/ui/button";

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

  return <EchoPage email={user.profile.email} onSignOut={signOut} />;
}

function EchoPage({ email, onSignOut }: { email: string | undefined; onSignOut: () => void }) {
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const callEcho = async () => {
    setError(null);
    setResult(null);
    try {
      const response = await apiFetch("/echo");
      if (!response.ok) {
        setError(`GET /echo failed: ${response.status}`);
        return;
      }
      const body = (await response.json()) as { message: string; sub: string };
      setResult(`${body.message} (sub: ${body.sub})`);
    } catch {
      setError("GET /echo failed: network error");
    }
  };

  return (
    <div className="flex min-h-screen flex-col gap-4 p-4">
      <header className="flex items-center justify-between">
        <h1 className="text-lg font-medium">chat-with-docs-bedrock</h1>
        <div className="flex items-center gap-3">
          <span className="text-sm text-slate-600">{email}</span>
          <Button variant="outline" onClick={onSignOut}>
            Sign out
          </Button>
        </div>
      </header>
      <main className="flex flex-col gap-2">
        <Button onClick={() => void callEcho()}>Call GET /echo</Button>
        {result !== null && <p className="text-sm text-slate-900">{result}</p>}
        {error !== null && <p className="text-sm text-red-600">{error}</p>}
      </main>
    </div>
  );
}
