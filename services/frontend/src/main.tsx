import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { CopilotKit } from "@copilotkit/react-core";
import { HttpAgent } from "@ag-ui/client";

import App from "./App";
import { AGENT_NAME } from "./state/UiState";
import { createConversation } from "./api/session";
import "./index.css";

// Bootstrap: allocate a backend conversation, then mount CopilotKit pointing
// at our AG-UI route. The conversation id doubles as the AG-UI thread_id, so
// every subsequent agent run lands on the same ChatSession.
//
// We use `agents__unsafe_dev_only` because we don't have a CopilotRuntime
// middleware (Next.js-only). For a Vite project this is the documented
// direct-AG-UI path — the prop name is intentionally alarming so production
// deployments don't accidentally rely on it without a runtime.
function Bootstrap() {
  const [agent, setAgent] = useState<HttpAgent | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    createConversation()
      .then((conv) => {
        if (cancelled) return;
        setAgent(
          new HttpAgent({
            url: "/api/agent",
            threadId: conv.id,
          }),
        );
      })
      .catch((err) => setError(String(err)));
    return () => {
      cancelled = true;
    };
  }, []);

  // CopilotKit injects a floating dev-console / promo widget as a
  // <cpk-web-inspector> custom element with a closed shadow root. The
  // `showDevConsole={false}` prop covers the console toggle but not the
  // promo bubble, and shadow-DOM content can't be styled via the page's
  // CSS cascade. Hide the host element itself whenever it appears.
  useEffect(() => {
    const hideCopilotInspectors = () => {
      for (const el of document.querySelectorAll("cpk-web-inspector")) {
        (el as HTMLElement).style.display = "none";
      }
    };
    hideCopilotInspectors();
    const obs = new MutationObserver(hideCopilotInspectors);
    obs.observe(document.documentElement, { childList: true, subtree: true });
    return () => obs.disconnect();
  }, []);

  if (error) {
    return (
      <div className="flex h-screen items-center justify-center bg-paper p-8 text-sm text-ink/70">
        Failed to start a conversation: {error}
      </div>
    );
  }
  if (!agent) {
    return (
      <div className="flex h-screen items-center justify-center bg-paper text-sm text-ink/50">
        starting…
      </div>
    );
  }

  return (
    <CopilotKit
      // CopilotKit requires *something* in `runtimeUrl` even when all agent
      // traffic is going through `agents__unsafe_dev_only`. The URL itself
      // is never hit in self-managed mode — the HttpAgent above owns the
      // wire — but the provider's runtime client throws at construction
      // without it.
      runtimeUrl="/api/agent"
      agent={AGENT_NAME}
      agents__unsafe_dev_only={{ [AGENT_NAME]: agent }}
      showDevConsole={false}
    >
      <App />
    </CopilotKit>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Bootstrap />
  </StrictMode>,
);
