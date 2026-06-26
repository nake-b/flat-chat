import { useEffect, useRef } from "react";

import { useCopilotChatInternal } from "@copilotkit/react-core";

import { getConversationMessages, getConversationState } from "../api/session";
import { useSessionState } from "../hooks/useSessionState";

// Reload recovery (renders nothing). On a resumed conversation, hydrates the
// frontend from the durable store over plain HTTP — NO agent turn:
//   - map/cards/active listing  ← GET /…/state  → useCoAgent setState
//   - chat transcript           ← GET /…/messages → setMessages
//
// `setMessages` comes from `useCopilotChatInternal` (exported, typed, and
// works WITHOUT a publicApiKey — the public `useCopilotChat` omits it, and the
// `<CopilotChat>` wrapper is otherwise opaque to history injection). If it's
// ever unavailable we still restore map/cards, and the backend is
// history-authoritative (it injects the stored history when the frontend sends
// only the new prompt), so the agent keeps full context regardless.
//
// Must live inside <CopilotKit> (uses useCoAgent + useCopilotChatInternal).
export function ConversationRecovery({
  conversationId,
  resumed,
}: {
  conversationId: string;
  resumed: boolean;
}) {
  const { setState } = useSessionState();
  const { setMessages } = useCopilotChatInternal();
  // Run once per conversation id (guards React StrictMode's double-invoke and
  // any re-render); a "New conversation" remounts with a fresh id.
  const hydratedFor = useRef<string | null>(null);

  useEffect(() => {
    if (!resumed || hydratedFor.current === conversationId) return;
    hydratedFor.current = conversationId;

    let cancelled = false;
    void (async () => {
      const [state, messages] = await Promise.all([
        getConversationState(conversationId).catch(() => null),
        getConversationMessages(conversationId).catch(() => []),
      ]);
      if (cancelled) return;
      if (state) setState(state);
      if (messages.length && typeof setMessages === "function") {
        // AG-UI user/assistant messages are plain {id, role, content} objects.
        setMessages(
          messages.map((m) => ({
            id: m.id,
            role: m.role,
            content: m.content,
          })) as never,
        );
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [conversationId, resumed, setState, setMessages]);

  return null;
}
