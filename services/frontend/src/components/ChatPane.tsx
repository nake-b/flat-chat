import { useMemo, useState, type ReactNode } from "react";

import { useCopilotChatInternal } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";

import { AccountMenu } from "./AccountMenu";
import { useBookmarkSidebarOpen } from "../hooks/useBookmarkSidebarOpen";
import { useSidebarOpen } from "../hooks/useSidebarOpen";
import { useToolStatusPills, useThinkingPillInStream } from "../hooks/useToolStatus";
import { useRecovery } from "../state/recovery";
import {
  CAPABILITIES_HREF,
  STARTER_HEADLINES,
  STARTER_INTROS,
  STARTER_PROMPTS,
  pickRandom,
  pickStratified,
} from "../state/starterPrompts";

const CAPABILITIES_PROMPT =
  "What can you do right now? Please summarize your current capabilities and the world context data you can access at the moment.";

// Custom renderer for links inside assistant messages, wired via CopilotChat's
// `markdownTagRenderers`. The `#capabilities` link (in the initial bubble) sends
// the capabilities prompt as a real user turn instead of navigating; every other
// link renders normally. This replaces a global document click listener that was
// coupled to CopilotKit's internal `.copilotKitAssistantMessage` DOM class.
// Module-scope so react-markdown keeps a stable component identity across
// renders; it lives inside the CopilotKit provider, so it can call the hook.
function AssistantLink({
  href,
  children,
}: {
  href?: string;
  children?: ReactNode;
}) {
  const { sendMessage } = useCopilotChatInternal();
  if (href === CAPABILITIES_HREF) {
    return (
      <a
        href={href}
        onClick={(event) => {
          event.preventDefault();
          void sendMessage(
            { id: crypto.randomUUID(), role: "user", content: CAPABILITIES_PROMPT },
            { followUp: true },
          );
        }}
      >
        {children}
      </a>
    );
  }
  return (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  );
}

const markdownTagRenderers = { a: AssistantLink };

export function ChatPane({ onNewChat }: { onNewChat: () => void }) {
  const { messages, sendMessage } = useCopilotChatInternal();
  // Headline + the three example prompts are picked once on mount and stay
  // stable for the life of the (empty) thread — no reroll. They stop rendering
  // as soon as the first user message lands (see `starterOpen`). The three are
  // sampled from DISTINCT capability categories so the pills showcase different
  // things the app can do, not near-duplicates.
  const [starterHeadline] = useState(() => pickRandom(STARTER_HEADLINES));
  const [starterIntro] = useState(() => pickRandom(STARTER_INTROS));
  const [starterPrompts] = useState(() => pickStratified(STARTER_PROMPTS, 3));

  // Send a starter prompt as a real user turn via CopilotKit's programmatic
  // send API (`followUp: true` runs the agent). No DOM scraping — the message
  // flows through `POST /api/agent` like any typed prompt, so it's persisted +
  // reload-safe and the derived `starterOpen` dismisses the cards.
  const sendPrompt = (prompt: string) =>
    void sendMessage(
      { id: crypto.randomUUID(), role: "user", content: prompt },
      { followUp: true },
    );

  // Show starters only while the thread is empty AND we already know the
  // history (`historyLoaded`). On a resumed thread CopilotKit mounts with
  // `messages: []` before ConversationRecovery hydrates the transcript, so
  // gating on `historyLoaded` stops the cards flashing then vanishing.
  const historyLoaded = useRecovery((s) => s.historyLoaded);
  const noUserMessage = useMemo(
    () =>
      !messages.some(
        (m) =>
          m.role === "user" &&
          typeof m.content === "string" &&
          m.content.trim().length > 0,
      ),
    [messages],
  );
  const starterOpen = historyLoaded && noUserMessage;

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
          onClick={onNewChat}
          aria-label="New chat"
          title="New chat"
          className="flex h-10 w-10 items-center justify-center text-ink-soft transition-colors hover:text-red"
        >
          <svg
            viewBox="0 0 20 20"
            width="26"
            height="26"
            aria-hidden
            fill="none"
            stroke="currentColor"
            strokeWidth="1.9"
            strokeLinecap="round"
          >
            <line x1="10" y1="4" x2="10" y2="16" />
            <line x1="4" y1="10" x2="16" y2="10" />
          </svg>
        </button>
        <button
          type="button"
          onClick={toggleSidebar}
          aria-label="Open conversation list"
          aria-expanded={open}
          aria-controls="conversation-sidebar"
          className="flex h-10 w-10 items-center justify-center text-ink-soft transition-colors hover:text-red"
        >
          <svg
            viewBox="0 0 20 20"
            width="26"
            height="26"
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
          className="flex h-10 w-10 items-center justify-center text-ink-soft transition-colors hover:text-red"
        >
          {/* Home with a heart inside — the bookmarks affordance, echoing the
              red save-heart on cards. House strokes follow currentColor so
              hover recolours them; the heart fill is fixed Berliner Rot. */}
          <svg
            viewBox="0 0 28 28"
            width="26"
            height="26"
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

      {starterOpen ? (
        <div className="px-5 pt-2 pb-1">
          <p className="mb-1.5 font-sans text-sm text-ink-ghost">{starterHeadline}</p>
          {/* Chat-bubble cards — an inline emoji + a short descriptive label; the
              emoji + full prompt is sent on click (emoji prepended from the same
              field so the sent message consistently leads with it). Three-across
              grid so they span the row, one per distinct capability (pickStratified). */}
          <div className="grid grid-cols-3 gap-2">
            {starterPrompts.map((p) => (
              <button
                key={p.label}
                type="button"
                title={p.prompt}
                onClick={() => sendPrompt(`${p.emoji} ${p.prompt}`)}
                className="rounded-[14px_14px_14px_4px] border border-[#dedede] bg-[#ececec] px-3 py-2 text-left text-sm leading-snug text-ink-soft transition-colors hover:bg-[#e3e3e3]"
              >
                <span className="mr-1" aria-hidden>
                  {p.emoji}
                </span>
                {p.label}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-hidden">
        <CopilotChat
          className="h-full"
          markdownTagRenderers={markdownTagRenderers}
          labels={{
            title: "",
            initial: starterIntro,
            placeholder: "Describe your apartment…",
          }}
        />
        {thinkingPill}
      </div>
    </div>
  );
}
