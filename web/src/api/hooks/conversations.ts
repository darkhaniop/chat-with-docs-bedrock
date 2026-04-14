import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiJson } from "../client";
import type { Conversation, Message, Page, PostMessageAccepted } from "../types";

function conversationsKey(projectId: string) {
  return ["projects", projectId, "conversations"] as const;
}

export function messagesKey(conversationId: string) {
  return ["conversations", conversationId, "messages"] as const;
}

export function useConversations(projectId: string | null) {
  return useQuery({
    queryKey: conversationsKey(projectId ?? ""),
    queryFn: () => apiJson<Page<Conversation>>(`/projects/${projectId}/conversations`),
    enabled: projectId !== null,
  });
}

export function useCreateConversation(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiJson<Conversation>(`/projects/${projectId}/conversations`, {
        method: "POST",
        body: JSON.stringify({}),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: conversationsKey(projectId) });
    },
  });
}

export function useActiveConversation(projectId: string | null) {
  const { data, isLoading } = useConversations(projectId);
  const createConversation = useCreateConversation(projectId ?? "");
  const existing = data?.items[0] ?? null;

  return {
    conversation: existing,
    isLoading: isLoading || createConversation.isPending,
    ensureConversation: async (): Promise<Conversation> => {
      if (existing !== null) return existing;
      return createConversation.mutateAsync();
    },
  };
}

export function useMessages(conversationId: string | null) {
  return useQuery({
    queryKey: messagesKey(conversationId ?? ""),
    queryFn: () => apiJson<Page<Message>>(`/conversations/${conversationId}/messages`),
    enabled: conversationId !== null,
  });
}

export function usePostMessage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ conversationId, text }: { conversationId: string; text: string }) =>
      apiJson<PostMessageAccepted>(`/conversations/${conversationId}/messages`, {
        method: "POST",
        body: JSON.stringify({ text }),
      }),
    onSuccess: (result, variables) => {
      queryClient.setQueryData<Page<Message>>(messagesKey(variables.conversationId), (current) => ({
        items: [...(current?.items ?? []), result.userMessage],
        nextCursor: current?.nextCursor ?? null,
      }));
    },
  });
}

export function useCancelMessage(conversationId: string) {
  return useMutation({
    mutationFn: (messageId: string) =>
      apiJson<{ status: string }>(`/conversations/${conversationId}/messages/${messageId}/cancel`, {
        method: "POST",
      }),
  });
}
