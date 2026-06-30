import { useEffect, useMemo, useRef, useState } from "react";

import { useCopilotChatInternal } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";

import { useToolStatusPills, useThinkingPillInStream } from "../hooks/useToolStatus";
import { useAuth } from "../hooks/useAuth";

const STARTER_HEADLINES = [
  "New here? Ask me something:",
  "A few examples of what I can do:",
  "What can I do for you? Ask:",
] as const;

const CAPABILITIES_PROMPT =
  "What can you do right now? Please summarize your current capabilities and the world context data you can access at the moment.";

const STARTER_PROMPTS = [
  "🏡 I am looking for a 2 rooms apartment for up to 1200€ with a balcony. It would ideally be located in a quiet and green area.",
  "We're moving with a dog 🐶. Please find dog friendly apartments close to a large park, so that we can go out regularly. We're looking for 2-3 rooms, 1800€ maximum.",
  "📍 Show me all flats 500m around Alexanderplatz. Price and size do not matter.",
  "Show me apartments 2km around UberArena. 🎶",
  "👨‍👩‍👧‍👦 Please find a 2-3 rooms apartment located in Pankow or Reinickendorf for less than 1500€. It must be child friendly and have a playground close by.",
  "Look for a apartment with disability parking close by.",
  "🌾 I'm looking a new residence in a low populated area. 1-2 rooms would be ideal, price does not matter.",
  "🗺️ Please visualise all available apartments around the S-Bahn Ring.",
  "🚇 What flats do you have along the U7?",
  "🚴 Find me a potential new home in biking distance to FU-Berlin.",
  "Find me a residence that is located as close as possible to a lake. 🌊",
  "🌳 What are the biggest parks in Berlin? Do you find any rooms close to them?",
  "💶 What can I get for a budget up to 800€? Any student friendly accommodations close to a University?",
  "🎓 Please find me a student friendly apartment in Steglitz-Zehlendorf. And what buses stop there?",
  "Please find a flat with 2-3s bedrooms for a future family 👶. Filter for low-populated areas and a lot of greenery.",
  "🏥 Show me apartments with a hospital nearby and compare how far the closest hospitals are.",
  "Find me listings close to a Kita and a Grundschule at the same time.",
  "Which flats are in quieter areas 🤫 and still close to parks?",
  "I want apartments near tram or bus stops with very short walking distance.",
  "Show me offers outside the S-Bahn ring but still close to water.",
] as const;

function pickRandomItems<T>(items: readonly T[], count: number): T[] {
  const shuffled = [...items];
  for (let i = shuffled.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  return shuffled.slice(0, Math.min(count, shuffled.length));
}

function submitPromptToComposer(prompt: string) {
  const textarea = document.querySelector(
    ".copilotKitInput textarea",
  ) as HTMLTextAreaElement | null;
  if (!textarea) return;

  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype,
    "value",
  )?.set;
  setter?.call(textarea, prompt);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));

  // CopilotKit may enable the send button on the next paint after `input`.
  // Retry a few frames so one click on an example reliably sends to the agent.
  const tryClickSubmit = (attempt = 0) => {
    const submit = document.querySelector(
      ".copilotKitInput button, .copilotKitInput button[type='submit']",
    ) as HTMLButtonElement | null;
    if (submit && !submit.disabled) {
      submit.click();
      return;
    }
    if (attempt >= 8) return;
    requestAnimationFrame(() => tryClickSubmit(attempt + 1));
  };

  requestAnimationFrame(() => {
    if (textarea.form?.requestSubmit) {
      textarea.form.requestSubmit();
      return;
    }
    tryClickSubmit();
  });
}

function isGeneralCapabilitiesPrompt(text: string): boolean {
  const t = text.toLowerCase();
  return (
    t.includes("what can you do") ||
    t.includes("what can i do") ||
    t.includes("what skills do you have") ||
    t.includes("what do you know") ||
    t.includes("what are your capabilities") ||
    t.includes("what can i ask") ||
    t.includes("which data can you access right now") ||
    t === CAPABILITIES_PROMPT.toLowerCase()
  );
}

function isApartmentFilterPrompt(text: string): boolean {
  const t = text.toLowerCase();
  const filterSignals = [
    "apartment",
    "apartments",
    "flat",
    "flats",
    "wohnung",
    "room",
    "rooms",
    "bedroom",
    "budget",
    "rent",
    "price",
    "district",
    "balcony",
    "furnished",
    "wbs",
    "quiet",
    "green",
    "park",
    "lake",
    "ring",
    "u7",
    "s-bahn",
    "tram",
    "bus",
    "kita",
    "school",
    "hospital",
    "near ",
    "around ",
    " km",
    " m",
    "€",
    "eur",
  ];
  return filterSignals.some((signal) => t.includes(signal));
}

export function ChatPane({
  onNewConversation,
}: {
  onNewConversation: () => void;
}) {
  const userEmail = useAuth((s) => s.user?.email);
  const logout = useAuth((s) => s.logout);
  const { messages } = useCopilotChatInternal();
  const [starterOpen, setStarterOpen] = useState(true);
  const [starterMessageCount, setStarterMessageCount] = useState(0);
  const [starterHeadline, setStarterHeadline] = useState(
    () => pickRandomItems(STARTER_HEADLINES, 1)[0],
  );
  const [starterPrompts, setStarterPrompts] = useState(() =>
    pickRandomItems(STARTER_PROMPTS, 3),
  );
  const processedUserMessages = useRef(0);

  const userMessages = useMemo(
    () =>
      messages.filter(
        (m) =>
          m.role === "user" &&
          typeof m.content === "string" &&
          m.content.trim().length > 0,
      ) as Array<{ content: string }>,
    [messages],
  );

  useEffect(() => {
    if (userMessages.length <= processedUserMessages.current) return;

    let nextOpen = starterOpen;
    let nextCount = starterMessageCount;
    let shouldReroll = false;

    for (let i = processedUserMessages.current; i < userMessages.length; i += 1) {
      const text = userMessages[i].content.trim();

      if (isGeneralCapabilitiesPrompt(text)) {
        if (!nextOpen) {
          nextOpen = true;
          shouldReroll = true;
        }
        nextCount = 0;
        continue;
      }

      if (isApartmentFilterPrompt(text)) {
        nextOpen = false;
        continue;
      }

      nextCount += 1;
      if (nextCount >= 2) nextOpen = false;
    }

    processedUserMessages.current = userMessages.length;
    if (shouldReroll) {
      setStarterHeadline(pickRandomItems(STARTER_HEADLINES, 1)[0]);
      setStarterPrompts(pickRandomItems(STARTER_PROMPTS, 3));
    }
    if (nextOpen !== starterOpen) setStarterOpen(nextOpen);
    if (nextCount !== starterMessageCount) setStarterMessageCount(nextCount);
  }, [userMessages, starterOpen, starterMessageCount]);

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      const trigger = target?.closest(
        ".copilotKitAssistantMessage a[href='#capabilities']",
      );
      if (!trigger) return;
      event.preventDefault();
      submitPromptToComposer(CAPABILITIES_PROMPT);
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
          <p className="mb-2 font-sans text-sm text-ink-ghost">{starterHeadline}</p>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
            {starterPrompts.map((prompt) => (
              <button
                key={prompt}
                type="button"
                onClick={() => submitPromptToComposer(prompt)}
                className="min-h-[72px] rounded-[14px_14px_14px_4px] border border-[#dedede] bg-[#ececec] px-3 py-2 text-left text-sm leading-snug text-ink-soft transition-colors hover:bg-[#e3e3e3]"
              >
                {prompt}
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
