// Conversation lifecycle + reload-recovery reads.
// The conversation id doubles as the AG-UI `thread_id` so the backend routes
// agent runs to the right ChatSession.

import type { SessionState } from "../state/SessionState";

export interface Conversation {
  id: string;
  created_at: string;
}

// One AG-UI message as returned by GET /messages — the full transcript shape
// (text + tool calls + tool results), produced by Pydantic AI's
// `AGUIAdapter.dump_messages` (camelCase via `by_alias`). Mirrors the shape
// CopilotKit's `setMessages` consumes so the transcript is restored verbatim;
// tool "finishes" re-render through the same wildcard tool-pill path as live.
export interface StoredMessage {
  id: string;
  role: "user" | "assistant" | "tool" | "system" | string;
  content?: string | null;
  toolCalls?: {
    id: string;
    type: "function";
    function: { name: string; arguments: string };
  }[];
  toolCallId?: string;
}

export async function createConversation(): Promise<Conversation> {
  const res = await fetch("/api/conversations", {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error(`Failed to create conversation: ${res.status}`);
  }
  return (await res.json()) as Conversation;
}

// Latest SessionState snapshot — the map/cards/active-listing recovery primitive.
// Returns null when the conversation no longer exists (404), which the caller
// uses to detect a stale id and fall back to creating a fresh conversation.
export async function getConversationState(
  id: string,
): Promise<SessionState | null> {
  const res = await fetch(
    `/api/conversations/${encodeURIComponent(id)}/state`,
    { credentials: "include" },
  );
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(`Failed to load conversation state: ${res.status}`);
  }
  return (await res.json()) as SessionState;
}

// User + assistant turns for transcript restore. 404 → [] (treated as empty).
export async function getConversationMessages(
  id: string,
): Promise<StoredMessage[]> {
  const res = await fetch(
    `/api/conversations/${encodeURIComponent(id)}/messages`,
    { credentials: "include" },
  );
  if (res.status === 404) return [];
  if (!res.ok) {
    throw new Error(`Failed to load conversation messages: ${res.status}`);
  }
  return (await res.json()) as StoredMessage[];
}
