import { CopilotChat } from "@copilotkit/react-ui";

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
      <header className="relative border-b-2 border-red px-7 pb-4 pt-6 text-center">
        <button
          type="button"
          onClick={toggleSidebar}
          aria-label="Open conversation list"
          aria-expanded={open}
          aria-controls="conversation-sidebar"
          className="absolute left-4 top-5 flex h-7 w-7 items-center justify-center text-ink-soft transition-colors hover:text-red"
        >
          {/* Inline SVG — the rest of the components also use inline icons,
              no icon library is in package.json. */}
          <svg
            viewBox="0 0 20 20"
            width="18"
            height="18"
            aria-hidden
            fill="none"
            stroke="currentColor"
            strokeWidth="1.75"
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
          className="absolute left-12 top-5 flex h-7 w-7 items-center justify-center text-ink-soft transition-colors hover:text-amber-500"
        >
          {/* Folder with a yellow star — bookmark tab affordance. Folder strokes
              follow currentColor so hover still recolours them; the star fill
              is hard-coded amber to stay yellow across hover states. */}
          <svg
            viewBox="0 0 20 20"
            width="18"
            height="18"
            aria-hidden
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M2.5 6.2V4.5a1 1 0 0 1 1-1h4l1.5 1.6h7.5a1 1 0 0 1 1 1V15a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1V6.2z" />
            <polygon
              points="10 8 11.2 10.5 14 10.7 11.9 12.5 12.5 15.2 10 13.8 7.5 15.2 8.1 12.5 6 10.7 8.8 10.5 10 8"
              fill="#FBBF24"
              stroke="#B7860B"
              strokeWidth="0.9"
            />
          </svg>
        </button>
        <h1 className="font-sans text-[2rem] font-extrabold leading-none tracking-[-0.035em] text-ink">
          Flat<span className="px-1 text-red">·</span>Chat
        </h1>
        <span className="mt-2.5 inline-block font-mono text-[10px] uppercase tracking-[0.18em] text-ink-soft">
          the Berlin Real Estate (AI) Agent
        </span>
      </header>

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
