// Conversation-list endpoint for the sidebar.
// Mirrors the style of `api/session.ts` — plain `fetch`, typed return, no query lib.

export interface ConversationSummary {
  id: string;
  title: string | null; // null until the background title-gen task lands
  created_at: string;
  updated_at: string; // drives sort + the relative-time label
}

export async function listConversations(
  signal?: AbortSignal,
): Promise<ConversationSummary[]> {
  const res = await fetch("/api/conversations", { signal });
  if (!res.ok) {
    throw new Error(`Failed to list conversations: ${res.status}`);
  }
  return (await res.json()) as ConversationSummary[];
}

// Hard-deletes the conversation on the backend (CASCADEs to messages +
// session_state). Returns void; the caller refetches the list afterwards.
export async function deleteConversation(id: string): Promise<void> {
  const res = await fetch(`/api/conversations/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  // 204 No Content on success; anything else is a real failure.
  if (!res.ok) {
    throw new Error(`Failed to delete conversation: ${res.status}`);
  }
}
