import { formatRelative } from "../utils/relativeTime";
import type { ConversationSummary } from "../api/conversations";

interface Props {
  row: ConversationSummary;
  active: boolean;
  onSelect: (id: string) => void;
  onDeleteRequest: (id: string) => void;
}

// One row in the conversation sidebar. The outer element is a `<div>` (not a
// button) so it can host TWO buttons: the row-select button covering the row's
// text, and the trash button at the right edge. Nested <button>s are illegal
// HTML; siblings + stopPropagation is the correct pattern.
//
// Trash icon appears on hover (`group-hover:opacity-100`) AND on keyboard
// focus (`focus-within:opacity-100`) so the affordance is reachable via Tab.
export function ConversationSidebarItem({
  row,
  active,
  onSelect,
  onDeleteRequest,
}: Props) {
  const title = row.title ?? "Untitled";
  const timestamp = formatRelative(row.updated_at);

  const wrapperClasses = active
    ? "border-red bg-red-tint text-ink"
    : "border-transparent text-ink-soft hover:bg-paper-dim hover:text-ink";

  return (
    <div
      className={
        "group relative border-l-2 transition-colors " + wrapperClasses
      }
    >
      <button
        type="button"
        onClick={() => onSelect(row.id)}
        aria-current={active ? "page" : undefined}
        className="w-full px-4 py-2.5 pr-10 text-left"
      >
        <div
          className={
            "truncate font-sans text-sm " +
            (row.title === null ? "italic text-ink-soft" : "")
          }
        >
          {title}
        </div>
        <div className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-ghost">
          {timestamp}
        </div>
      </button>
      <button
        type="button"
        aria-label={`Delete conversation: ${title}`}
        onClick={(e) => {
          // Belt-and-braces: nested <button>s aren't structurally possible
          // here so a real "trigger the row select" only happens via event
          // bubbling. Stop it anyway in case future layout puts these inside
          // a shared click handler.
          e.stopPropagation();
          onDeleteRequest(row.id);
        }}
        className="absolute right-2 top-1/2 -translate-y-1/2 flex h-7 w-7 items-center justify-center text-ink-ghost opacity-0 transition-opacity hover:text-red focus:opacity-100 group-hover:opacity-100"
      >
        <svg
          viewBox="0 0 20 20"
          width="14"
          height="14"
          aria-hidden
          fill="none"
          stroke="currentColor"
          strokeWidth="1.75"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          {/* Lid */}
          <line x1="3" y1="6" x2="17" y2="6" />
          <line x1="8" y1="3" x2="12" y2="3" />
          {/* Bin body */}
          <path d="M5 6l1 11h8l1-11" />
          {/* Vertical strokes inside */}
          <line x1="8.5" y1="9" x2="8.5" y2="14" />
          <line x1="11.5" y1="9" x2="11.5" y2="14" />
        </svg>
      </button>
    </div>
  );
}
