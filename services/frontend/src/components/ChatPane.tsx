import { CopilotChat } from "@copilotkit/react-ui";

import { AccountMenu } from "./AccountMenu";
import { useBookmarkSidebarOpen } from "../hooks/useBookmarkSidebarOpen";
import { useSidebarOpen } from "../hooks/useSidebarOpen";
import { useToolStatusPills, useThinkingPillInStream } from "../hooks/useToolStatus";

export function ChatPane() {
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

  const open = useSidebarOpen((s) => s.open);
  const toggleSidebar = useSidebarOpen((s) => s.toggleSidebar);
  const bookmarkOpen = useBookmarkSidebarOpen((s) => s.open);
  const toggleBookmarks = useBookmarkSidebarOpen((s) => s.toggleSidebar);

  return (
    <div className="flex h-full flex-col bg-paper">
      {/* Row 1 — centered wordmark + tagline. */}
      <div className="flex flex-col items-center border-b border-paper-rule px-7 pb-3 pt-6 text-center">
        <h1 className="font-sans text-[2rem] font-extrabold leading-none tracking-[-0.035em] text-ink">
          Flat<span className="px-1 text-red">·</span>Chat
        </h1>
        <span className="mt-2.5 inline-block font-mono text-[10px] uppercase tracking-[0.18em] text-ink-soft">
          the Berlin Real Estate (AI) Agent
        </span>
      </div>

      {/* Row 2 — utility bar: nav icons on the left (conversations, bookmarks),
          account dropdown on the right. Inline SVGs — no icon library in
          package.json. */}
      <div className="flex items-center gap-1 border-b-2 border-red px-3 py-1.5">
        <button
          type="button"
          onClick={toggleSidebar}
          aria-label="Open conversation list"
          aria-expanded={open}
          aria-controls="conversation-sidebar"
          className="flex h-9 w-9 items-center justify-center text-ink-soft transition-colors hover:text-red"
        >
          <svg
            viewBox="0 0 20 20"
            width="24"
            height="24"
            aria-hidden
            fill="none"
            stroke="currentColor"
            strokeWidth="1.9"
            strokeLinecap="round"
          >
            <line x1="3" y1="6" x2="17" y2="6" />
            <line x1="3" y1="10" x2="17" y2="10" />
            <line x1="3" y1="14" x2="17" y2="14" />
          </svg>
        </button>
        <button
          type="button"
          onClick={toggleBookmarks}
          aria-label="Open bookmarks"
          aria-expanded={bookmarkOpen}
          aria-controls="bookmark-sidebar"
          className="flex h-9 w-9 items-center justify-center text-ink-soft transition-colors hover:text-red"
        >
          {/* Home with a heart inside — the bookmarks affordance, echoing the
              red save-heart on cards. House strokes follow currentColor so
              hover recolours them; the heart fill is fixed Berliner Rot. */}
          <svg
            viewBox="0 0 28 28"
            width="24"
            height="24"
            aria-hidden
            fill="none"
            stroke="currentColor"
            strokeWidth="1.9"
            strokeLinejoin="round"
          >
            <path d="M5 13.5 L14 5 L23 13.5 V24 H5 Z" />
            <path
              d="M14 21.2c-2.6-1.7-4.3-3.2-4.3-5.1a2.2 2.2 0 0 1 4.3-.8 2.2 2.2 0 0 1 4.3.8c0 1.9-1.7 3.4-4.3 5.1z"
              fill="#E4003C"
              stroke="none"
            />
          </svg>
        </button>
        <div className="flex-1" />
        <AccountMenu />
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
