import { CopilotChat } from "@copilotkit/react-ui";
import { useCoAgentStateRender } from "@copilotkit/react-core";

import { AGENT_NAME, type UiState } from "../state/UiState";
import { useUiState } from "../hooks/useUiState";

// Berlin Bezirke + common Ortsteile — used to enrich the in-flight
// status label with the district the user typed about, since the
// backend can't push a "Searching {district}…" log mid-tool (state
// snapshots only fire on tool return). Match is case-insensitive,
// boundary-aware. Order matters for compound names: hyphenated
// double-Bezirke first so "Friedrichshain-Kreuzberg" wins over
// the bare "Kreuzberg" substring.
const BERLIN_PLACES = [
  "Friedrichshain-Kreuzberg",
  "Charlottenburg-Wilmersdorf",
  "Steglitz-Zehlendorf",
  "Tempelhof-Schöneberg",
  "Treptow-Köpenick",
  "Marzahn-Hellersdorf",
  "Mitte",
  "Pankow",
  "Spandau",
  "Neukölln",
  "Lichtenberg",
  "Reinickendorf",
  "Kreuzberg",
  "Friedrichshain",
  "Prenzlauer Berg",
  "Wedding",
  "Schöneberg",
  "Charlottenburg",
  "Wilmersdorf",
  "Steglitz",
  "Zehlendorf",
  "Tempelhof",
  "Köpenick",
  "Treptow",
  "Marzahn",
  "Hellersdorf",
  "Lichterfelde",
  "Moabit",
  "Friedenau",
];

function extractDistrict(text: string): string | null {
  for (const p of BERLIN_PLACES) {
    const re = new RegExp(`\\b${p.replace(/-/g, "[-\\s]?")}\\b`, "i");
    if (re.test(text)) return p;
  }
  return null;
}

export function ChatPane() {
  const { running } = useUiState();

  useCoAgentStateRender<UiState>({
    name: AGENT_NAME,
    render: ({ state }) => {
      const lastLog = state?.tool_logs?.[state.tool_logs.length - 1] ?? null;

      // If the tool has already pushed a log entry ("Found 7 apartments in
      // Kreuzberg."), show that. Otherwise we're in the LLM thinking /
      // pre-tool-call window — best-effort enrich "Searching…" with the
      // district from the user's last message so the label isn't naked.
      let label = lastLog;
      if (!label) {
        const lastUserMsg = lastUserMessageText();
        const district = lastUserMsg ? extractDistrict(lastUserMsg) : null;
        label = district ? `Searching ${district}…` : "Searching…";
      }
      return <ToolStatusInline label={label} active={running} />;
    },
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

// Pull the last user-message text out of the live chat DOM. CopilotKit's
// useCoAgentStateRender doesn't expose a hook for "the message that
// triggered this run", so we read it from the DOM at render time.
function lastUserMessageText(): string | null {
  if (typeof document === "undefined") return null;
  const userMsgs = document.querySelectorAll(".copilotKitUserMessage");
  const last = userMsgs[userMsgs.length - 1] as HTMLElement | undefined;
  return last?.innerText?.trim() ?? null;
}

function ToolStatusInline({
  label,
  active,
}: {
  label: string;
  active: boolean;
}) {
  return (
    <div
      className="fc-status-line my-2 flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-ink-soft"
      role="status"
      aria-live="polite"
    >
      <span
        className={`inline-block h-1.5 w-1.5 rounded-full bg-red ${
          active ? "animate-pulse" : ""
        }`}
        aria-hidden
      />
      {label}
    </div>
  );
}
