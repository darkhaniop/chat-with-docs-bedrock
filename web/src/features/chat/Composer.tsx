import { useState } from "react";
import { Button } from "../../components/ui/button";

const MAX_LENGTH = 8000; // docs/05-api-contracts.md: "Validates the text (1-8000 characters)."

export function Composer({
  disabled,
  onSend,
}: {
  disabled: boolean;
  onSend: (text: string) => void;
}) {
  const [text, setText] = useState("");

  const submit = () => {
    const trimmed = text.trim();
    if (trimmed.length === 0 || trimmed.length > MAX_LENGTH || disabled) return;
    onSend(trimmed);
    setText("");
  };

  return (
    <form
      className="flex items-end gap-2 border-t border-slate-200 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <textarea
        className="min-h-[2.5rem] flex-1 resize-none rounded-md border border-slate-300 p-2 text-sm"
        placeholder="Ask a question…"
        value={text}
        disabled={disabled}
        maxLength={MAX_LENGTH}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          // ⌘/Ctrl+Enter sends — plain Enter stays a newline (docs/06-frontend.md#accessibility
          // lists ⌘↵ send as a keyboard shortcut).
          if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            submit();
          }
        }}
      />
      <Button type="submit" disabled={disabled || text.trim().length === 0}>
        Send
      </Button>
    </form>
  );
}
