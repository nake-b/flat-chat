import { useMemo, useState, type ReactNode } from "react";

import { useCopilotChatInternal } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";

import { useToolStatusPills, useThinkingPillInStream } from "../hooks/useToolStatus";
import { useAuth } from "../hooks/useAuth";
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
