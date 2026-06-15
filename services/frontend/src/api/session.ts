// Allocates a new conversation on the backend and returns its id.
// The id doubles as the AG-UI `thread_id` so the backend can route subsequent
// agent runs to the right ChatSession.

export interface Conversation {
  id: string;
  created_at: string;
}

export async function createConversation(): Promise<Conversation> {
  const res = await fetch("/api/conversations", { method: "POST" });
  if (!res.ok) {
    throw new Error(`Failed to create conversation: ${res.status}`);
  }
  return (await res.json()) as Conversation;
}
