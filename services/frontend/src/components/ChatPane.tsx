import { CopilotChat } from "@copilotkit/react-ui";

import { useToolStatusPills, useThinkingPillInStream } from "../hooks/useToolStatus";

export function ChatPane({
  onNewConversation,
}: {
  onNewConversation: () => void;
}) {
  // One wildcard registration drives inline pills for every backend tool
  // call. The label per lifecycle phase lives in `state/toolStatus.ts`
  // (single source of UI copy). Adding a new tool = one entry there;
  // nothing changes here.
  useToolStatusPills();

  // Thinking pill injected as the LAST child of `.copilotKitMessagesContainer`
  // so it sits in the same vertical rhythm as the tool pills — directly below
  // the latest message. DOM-portal approach because CopilotKit's
  // `useCoAgentStateRender` anchors via a stale message-id claim bridge.
  const thinkingPill = useThinkingPillInStream();

  return (
    <div className="flex h-full flex-col bg-paper">
      <header className="border-b-2 border-red px-7 pb-4 pt-6 text-center">
        <h1 className="font-sans text-[2rem] font-extrabold leading-none tracking-[-0.035em] text-ink">
          Flat<span className="px-1 text-red">·</span>Chat
        </h1>
        <span className="mt-2.5 inline-block font-mono text-[10px] uppercase tracking-[0.18em] text-ink-soft">
          the Berlin Real Estate (AI) Agent
        </span>
      </header>

      {/* Slim action strip — right-aligned ghost link, separated from the
          centred title so it doesn't compete with the brand. */}
      <div className="flex items-center justify-end border-b border-paper-rule px-5 py-2">
        <button
          type="button"
          onClick={onNewConversation}
          title="Start a new conversation"
          className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-soft transition-colors hover:text-red"
        >
          + New chat
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-hidden">
        <CopilotChat
          className="h-full"
          labels={{
            title: "",
            initial:
              "Hi. Tell me what you want — 2 rooms in Kreuzberg under €1200 with a balcony, or just describe the vibe. I'll find it.",
            placeholder: "Describe your apartment…",
          }}
        />
        {thinkingPill}
      </div>
    </div>
  );
}
