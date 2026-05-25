import { CopilotChat } from "@copilotkit/react-ui";
import { useCoAgentStateRender } from "@copilotkit/react-core";

import { AGENT_NAME, type UiState } from "../state/UiState";
import { ThinkingSlot, useToolStatusPills } from "../hooks/useToolStatus";

export function ChatPane() {
  // One wildcard registration drives inline pills for every backend tool
  // call. The label per lifecycle phase lives in `state/toolStatus.ts`
  // (single source of UI copy). Adding a new tool = one entry there;
  // nothing changes here.
  useToolStatusPills();

  // "Thinking…" — appears when the agent is running but no tool pill is
  // currently executing. Same inline-injection mechanism as the tool pills
  // above so it sits in the same vertical rhythm in the chat thread.
  //
  // Needs its own hook (not folded into `useToolStatusPills`) because the
  // `running` flag comes from `useCoAgent` — the wildcard `useCopilotAction`
  // only fires per tool call and can't see the LLM-only thinking phase
  // between tool calls. ThinkingSlot self-suppresses while any tool pill is
  // active (zustand counter in useToolStatus.tsx).
  useCoAgentStateRender<UiState>({
    name: AGENT_NAME,
    render: () => <ThinkingSlot />,
  });

  return (
    <div className="flex h-full flex-col bg-paper">
      <header className="border-b-2 border-red px-7 pb-4 pt-6 text-center">
        <h1 className="font-sans text-[2rem] font-extrabold leading-none tracking-[-0.035em] text-ink">
          Flat<span className="px-1 text-red">·</span>Chat
        </h1>
        <span className="mt-2.5 inline-block font-mono text-[10px] uppercase tracking-[0.18em] text-ink-soft">
          Berlin apartment search
        </span>
      </header>

      <div className="min-h-0 flex-1 overflow-hidden">
        <CopilotChat
          className="h-full"
          labels={{
            title: "",
            initial:
              "Hi. Tell me what you want — 2BR Kreuzberg under €1200, an Altbau with light, close to a U-Bahn — and I'll find it.",
            placeholder: "Describe your apartment…",
          }}
        />
      </div>
    </div>
  );
}
