// Conversation-id persistence for reload recovery.
//
// The active conversation id (== AG-UI thread_id) is kept in TWO places so a
// page reload resumes the same backend conversation:
//   - the URL path `/c/{id}` — shareable / bookmarkable (nginx has the SPA
//     history fallback, so a hard reload at /c/{id} still serves index.html)
//   - localStorage — the durable fallback if the user lands on `/`
// The URL wins on read. Neither is the source of truth for *content* — that's
// the backend (GET /…/messages + /…/state); these only remember WHICH thread.

const STORAGE_KEY = "flatchat.conversationId";
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function fromPath(): string | null {
  const m = window.location.pathname.match(/^\/c\/([^/]+)\/?$/);
  return m && UUID_RE.test(m[1]) ? m[1] : null;
}

export function readStoredConversationId(): string | null {
  const fromUrl = fromPath();
  if (fromUrl) return fromUrl;
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return v && UUID_RE.test(v) ? v : null;
  } catch {
    return null;
  }
}

export function rememberConversationId(id: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // private-mode / disabled storage — the URL still carries the id.
  }
  const path = `/c/${id}`;
  if (window.location.pathname !== path) {
    window.history.replaceState(null, "", path);
  }
}
