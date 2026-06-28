import { StrictMode, useCallback, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { CopilotKit } from "@copilotkit/react-core";
import { HttpAgent } from "@ag-ui/client";

import App from "./App";
import { AGENT_NAME } from "./state/UiState";
import { ConversationRecovery } from "./components/ConversationRecovery";
import { LoginGate } from "./components/LoginGate";
import {
  createConversation,
  getConversationState,
} from "./api/session";
import {
  readStoredConversationId,
  rememberConversationId,
} from "./state/conversationId";
import { useHover } from "./hooks/useHover";
import { useSidebarOpen } from "./hooks/useSidebarOpen";
import "./index.css";

// Bootstrap: resolve the conversation thread, then mount CopilotKit pointing at
// our AG-UI route. The conversation id doubles as the AG-UI thread_id.
//
// Reload recovery: a stored id (URL `/c/{id}` or localStorage) is reused so the
// conversation survives refresh / cross-device. We verify it still exists
// (GET /…/state ≠ 404) before resuming — a stale id (DB wiped, different user)
// falls back to a fresh conversation, so /api/agent never 404s on an unknown
// thread. `<CopilotKit key={id}>` makes "New conversation" a clean remount
// (fresh useCoAgent state + empty chat).
//
// `agents__unsafe_dev_only` is the documented direct-AG-UI path for Vite (no
// CopilotRuntime middleware) — the prop name is intentionally alarming.
function Bootstrap() {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [resumed, setResumed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Hover is client-local ephemera (zustand singleton) — reset on mount so a
    // stale id can't ghost-highlight a card during the init round-trip.
    useHover.getState().reset();

    void (async () => {
      try {
        const existing = readStoredConversationId();
        if (existing) {
          // Verify the thread still exists before resuming.
          const state = await getConversationState(existing).catch(() => null);
          if (cancelled) return;
          if (state !== null) {
            rememberConversationId(existing);
            setResumed(true);
            setConversationId(existing);
            return;
          }
        }
        const conv = await createConversation();
        if (cancelled) return;
        rememberConversationId(conv.id);
        setResumed(false);
        setConversationId(conv.id);
      } catch (err) {
        if (!cancelled) setError(String(err));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const startNewConversation = useCallback(async () => {
    useHover.getState().reset();
    const conv = await createConversation();
    rememberConversationId(conv.id);
    setResumed(false);
    // Changing the key (below) remounts CopilotKit → fresh state + empty chat.
    setConversationId(conv.id);
    useSidebarOpen.getState().closeSidebar();
  }, []);

  const switchConversation = useCallback((id: string) => {
    // Switching from the sidebar goes through the same recovery path as a
    // page reload — `setResumed(true)` so ConversationRecovery hydrates state
    // + transcript on the next CopilotKit mount (key change below).
    useHover.getState().reset();
    rememberConversationId(id);
    setResumed(true);
    setConversationId(id);
    useSidebarOpen.getState().closeSidebar();
  }, []);

  // One HttpAgent per thread id. Recreated when the id changes (new conversation).
  const agent = useMemo(
    () =>
      conversationId
        ? new HttpAgent({ url: "/api/agent", threadId: conversationId })
        : null,
    [conversationId],
  );

  // CopilotKit injects a floating dev-console / promo widget as a
  // <cpk-web-inspector> custom element with a closed shadow root. Hide the host
  // element itself whenever it appears (CSS can't reach its shadow DOM).
  useEffect(() => {
    const hideCopilotInspectors = () => {
      for (const el of document.querySelectorAll("cpk-web-inspector")) {
        (el as HTMLElement).style.display = "none";
      }
    };
    hideCopilotInspectors();
    const obs = new MutationObserver(hideCopilotInspectors);
    obs.observe(document.body, { childList: true, subtree: false });
    return () => obs.disconnect();
  }, []);

  if (error) {
    return (
      <div className="flex h-screen items-center justify-center bg-paper p-8 text-sm text-ink/70">
        Failed to start a conversation: {error}
      </div>
    );
  }
  if (!agent || !conversationId) {
    return (
      <div className="flex h-screen items-center justify-center bg-paper text-sm text-ink/50">
        starting…
      </div>
    );
  }

  return (
    <CopilotKit
      key={conversationId}
      // CopilotKit requires *something* in `runtimeUrl` even in
      // `agents__unsafe_dev_only` mode — it's never hit (the HttpAgent owns the
      // wire) but the provider's runtime client throws at construction without it.
      runtimeUrl="/api/agent"
      agent={AGENT_NAME}
      agents__unsafe_dev_only={{ [AGENT_NAME]: agent }}
      showDevConsole={false}
    >
      <ConversationRecovery conversationId={conversationId} resumed={resumed} />
      <App
        conversationId={conversationId}
        onNewConversation={startNewConversation}
        onSwitchConversation={switchConversation}
      />
    </CopilotKit>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LoginGate>
      <Bootstrap />
    </LoginGate>
  </StrictMode>,
);
