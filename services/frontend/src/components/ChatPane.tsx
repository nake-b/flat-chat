import { useEffect, useMemo, useRef, useState } from "react";

import { useCopilotChatInternal } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";

import { useToolStatusPills, useThinkingPillInStream } from "../hooks/useToolStatus";
import { useAuth } from "../hooks/useAuth";
import {
  STARTER_HEADLINES,
  STARTER_PROMPTS,
  pickRandom,
  pickStratified,
} from "../state/starterPrompts";

const CAPABILITIES_PROMPT =
  "What can you do right now? Please summarize your current capabilities and the world context data you can access at the moment.";

export function ChatPane({
  onNewConversation,
}: {
  onNewConversation: () => void;
}) {
  const userEmail = useAuth((s) => s.user?.email);
  const logout = useAuth((s) => s.logout);
  const { messages, sendMessage } = useCopilotChatInternal();
  // Headline + the three example prompts are picked once on mount and stay
  // stable for the life of the (empty) thread — no reroll. They stop rendering
  // as soon as the first user message lands (see `starterOpen`). The three are
  // sampled from DISTINCT capability categories so the pills showcase different
  // things the app can do, not near-duplicates.
  const [starterHeadline] = useState(() => pickRandom(STARTER_HEADLINES));
  const [starterPrompts] = useState(() => pickStratified(STARTER_PROMPTS, 3));

  // Send a starter/capabilities prompt as a real user turn via CopilotKit's
  // programmatic send API (`followUp: true` runs the agent). No DOM scraping —
  // the message flows through `POST /api/agent` like any typed prompt, so it's
  // persisted + reload-safe and the derived `starterOpen` dismisses the cards.
  const sendPrompt = (prompt: string) =>
    void sendMessage(
      { id: crypto.randomUUID(), role: "user", content: prompt },
      { followUp: true },
    );
  // Keep the latest `sendMessage` in a ref so the document-level click listener
  // below can stay on `[]` deps (never re-subscribes) yet always call the
  // current send fn.
  const sendPromptRef = useRef(sendPrompt);
  sendPromptRef.current = sendPrompt;

  // Show starters only while the thread is empty. Once the user has sent
  // anything they're dismissed and don't come back — simpler and less
  // surprising than counting turns or heuristically classifying prompts.
  const starterOpen = useMemo(
    () =>
      !messages.some(
        (m) =>
          m.role === "user" &&
          typeof m.content === "string" &&
          m.content.trim().length > 0,
      ),
    [messages],
  );

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      const trigger = target?.closest(
        ".copilotKitAssistantMessage a[href='#capabilities']",
      );
      if (!trigger) return;
      event.preventDefault();
      sendPromptRef.current(CAPABILITIES_PROMPT);
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);
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

      {/* Slim action strip — signed-in identity on the left, actions on the
          right, separated from the centred title so it doesn't compete with
          the brand. */}
      <div className="flex items-center justify-between border-b border-paper-rule px-5 py-2">
        <span
          title={userEmail ?? undefined}
          className="truncate font-mono text-[10px] tracking-[0.08em] text-ink-ghost"
        >
          {userEmail ?? ""}
        </span>
        <div className="flex items-center gap-4">
          <button
            type="button"
            onClick={onNewConversation}
            title="Start a new conversation"
            className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-soft transition-colors hover:text-red"
          >
            + New chat
          </button>
          <button
            type="button"
            onClick={() => void logout()}
            title="Sign out"
            className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-soft transition-colors hover:text-red"
          >
            Sign out
          </button>
        </div>
      </div>

      {starterOpen ? (
        <div className="px-5 py-3">
          <p className="mb-2 font-sans text-xs text-ink-ghost">{starterHeadline}</p>
          {/* Compact capability pills — short label on the chip, full prompt
              sent on click. One per distinct capability (see pickStratified). */}
          <div className="flex flex-wrap gap-2">
            {starterPrompts.map((p) => (
              <button
                key={p.label}
                type="button"
                title={p.prompt}
                onClick={() => sendPrompt(p.prompt)}
                className="rounded-full border border-[#dedede] bg-[#ececec] px-3 py-1.5 text-left text-xs leading-snug text-ink-soft transition-colors hover:bg-[#e3e3e3]"
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-hidden">
        <CopilotChat
          className="h-full"
          labels={{
            title: "",
            initial:
              "Hi. Tell me what you want — 2 rooms in Kreuzberg under €1200 with a balcony, or just describe the vibe. I'll find it. If you're still unsure, just ask me [what I can do](#capabilities).",
            placeholder: "Describe your apartment…",
          }}
        />
        {thinkingPill}
      </div>
    </div>
  );
}
