import { CopilotChat } from "@copilotkit/react-ui";

import { useBookmarkSidebarOpen } from "../hooks/useBookmarkSidebarOpen";
import { useSidebarOpen } from "../hooks/useSidebarOpen";
import { useToolStatusPills, useThinkingPillInStream } from "../hooks/useToolStatus";
import { useAuth } from "../hooks/useAuth";

export function ChatPane() {
  const userEmail = useAuth((s) => s.user?.email);
  const logout = useAuth((s) => s.logout);
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
          className="absolute left-4 top-5 flex h-10 w-10 items-center justify-center text-ink-soft transition-colors hover:text-red"
        >
          {/* Inline SVG — the rest of the components also use inline icons,
              no icon library is in package.json. */}
          <svg
            viewBox="0 0 20 20"
            width="29"
            height="29"
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
          className="absolute left-16 top-5 flex h-10 w-10 items-center justify-center text-ink-soft transition-colors hover:text-amber-500"
        >
          {/* House with a big yellow star in the middle — bookmark tab
              affordance. House strokes follow currentColor so hover still
              recolours them; the star fill is hard-coded yellow to stay bright
              across hover states. */}
          <svg
            viewBox="0 0 20 20"
            width="29"
            height="29"
            aria-hidden
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            {/* Tall roof (peak y=2) + base lowered to y=15.5 gives a longer,
                un-squished house body. The star is centred in that body with
                clear gaps below the roofline and above the base. */}
            <path d="M3 7.5 10 2l7 5.5V14.5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7.5z" />
            <polygon
              points="10 6.7 10.78 8.93 13.14 8.98 11.26 10.41 11.94 12.67 10 11.32 8.06 12.67 8.75 10.41 6.86 8.98 9.22 8.93"
              fill="#FACC15"
              stroke="#CA8A04"
              strokeWidth="0.6"
              strokeLinejoin="round"
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

      {/* Slim action strip — signed-in identity on the left, sign-out on the
          right. "+ New chat" lives in the conversation sidebar, not here. */}
      <div className="flex items-center justify-between border-b border-paper-rule px-5 py-2">
        <span
          title={userEmail ?? undefined}
          className="truncate font-mono text-[10px] tracking-[0.08em] text-ink-ghost"
        >
          {userEmail ?? ""}
        </span>
        <button
          type="button"
          onClick={() => void logout()}
          title="Sign out"
          className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-soft transition-colors hover:text-red"
        >
          Sign out
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
