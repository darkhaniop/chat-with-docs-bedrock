import { useState } from "react";
import { useActiveConversation, useMessages, usePostMessage } from "../../api/hooks/conversations";
import { ApiError } from "../../api/errors";
import { Composer } from "./Composer";
import { MessageText } from "./MessageText";

const STATUS_LABEL: Record<string, string> = {
  BLOCKED: "Blocked by content safety",
  FAILED: "Failed to generate a response",
};

export function ChatPane({ projectId }: { projectId: string }) {
  const { conversation, isLoading, ensureConversation } = useActiveConversation(projectId);
  const { data: messages } = useMessages(conversation?.conversationId ?? null);
  const postMessage = usePostMessage();
  const [error, setError] = useState<string | null>(null);

  const handleSend = async (text: string) => {
    setError(null);
    try {
      const active = await ensureConversation();
      await postMessage.mutateAsync({ conversationId: active.conversationId, text });
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Something went wrong sending that message.",
      );
    }
  };

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex-1 overflow-y-auto p-3">
        {isLoading && <p className="text-sm text-slate-500">Loading conversation…</p>}
        {!isLoading && (messages === undefined || messages.items.length === 0) && (
          <p className="text-sm text-slate-500">Ask a question about this project's documents.</p>
        )}
        <ul className="flex flex-col gap-3">
          {messages?.items.map((message) => (
            <li
              key={message.messageId}
              className={
                message.role === "user"
                  ? "self-end rounded-lg bg-slate-900 px-3 py-2 text-sm text-white"
                  : "rounded-lg bg-slate-100 px-3 py-2 text-sm text-slate-900"
              }
            >
              {message.status === "BLOCKED" || message.status === "FAILED" ? (
                <span className="text-red-700">
                  {STATUS_LABEL[message.status]}
                  {message.text ? `: ${message.text}` : ""}
                </span>
              ) : (
                <MessageText text={message.text} citations={message.citations} />
              )}
            </li>
          ))}
        </ul>
        {error !== null && <p className="mt-2 text-sm text-red-600">{error}</p>}
      </div>
      <Composer disabled={postMessage.isPending} onSend={(text) => void handleSend(text)} />
    </div>
  );
}
