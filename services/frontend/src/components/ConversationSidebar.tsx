import { useEffect, useState } from "react";

import type { ConversationSummary } from "../api/conversations";
import type { ListStatus } from "../hooks/useConversationList";
import { ConfirmDialog } from "./ConfirmDialog";
import { ConversationSidebarItem } from "./ConversationSidebarItem";

interface Props {
  open: boolean;
  items: ConversationSummary[];
  status: ListStatus;
  activeId: string | null;
  onClose: () => void;
  onSwitch: (id: string) => void;
  onNewChat: () => void;
  onDelete: (id: string) => void;
}

// GPT-style slide-out conversation panel. Always mounted; the data-open attr
// toggles the CSS transform so both open AND close animate (mount/unmount would
// cut the close transition off). Backdrop is a sibling rendered conditionally.
//
// role="complementary" (not "dialog") because this is a nav sidebar — no focus
// trap; Esc closes via the keydown listener registered only when `open`.
export function ConversationSidebar({
  open,
  items,
  status,
  activeId,
  onClose,
  onSwitch,
  onNewChat,
  onDelete,
}: Props) {
  // `pendingDeleteId` opens the confirm modal and remembers which row is at
  // stake. The dialog's title (or "Untitled") is shown in the prompt for the
  // user to verify. Cleared on cancel or confirm.
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const pendingRow =
    pendingDeleteId !== null
      ? items.find((row) => row.id === pendingDeleteId)
      : null;

  useEffect(() => {
    // Escape closes the sidebar — but only when the confirm modal isn't open
    // (the modal owns Escape while it's showing).
    if (!open || pendingDeleteId !== null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, pendingDeleteId]);

  const confirmDelete = () => {
    if (pendingDeleteId === null) return;
    onDelete(pendingDeleteId);
    setPendingDeleteId(null);
  };

  return (
    <>
      {open && (
        <button
          type="button"
          aria-label="Dismiss conversation list"
          tabIndex={-1}
          onClick={onClose}
          data-testid="sidebar-backdrop"
          className="fixed inset-0 z-40 cursor-default bg-ink/40 transition-opacity duration-200"
        />
      )}
      <aside
        id="conversation-sidebar"
        role="complementary"
        aria-label="Conversations"
        aria-hidden={!open}
        data-open={open}
        className={
          "fixed left-0 top-0 z-50 flex h-screen w-[300px] flex-col border-r " +
          "border-paper-rule bg-paper transition-transform duration-300 ease-snap " +
          "-translate-x-full data-[open=true]:translate-x-0"
        }
      >
        <div className="flex items-center justify-between border-b border-paper-rule px-4 py-3">
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-soft">
            Conversations
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close conversation list"
            className="font-mono text-xs text-ink-soft transition-colors hover:text-red"
          >
            ×
          </button>
        </div>

        <button
          type="button"
          onClick={onNewChat}
          className="border-b border-paper-rule px-4 py-3 text-left font-mono text-[10px] uppercase tracking-[0.14em] text-ink-soft transition-colors hover:bg-paper-dim hover:text-red"
        >
          + New chat
        </button>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {status === "loading" && items.length === 0 ? (
            <SidebarSkeleton />
          ) : status === "error" && items.length === 0 ? (
            <SidebarMessage>Couldn't load conversations</SidebarMessage>
          ) : items.length === 0 ? (
            <SidebarMessage>No conversations yet</SidebarMessage>
          ) : (
            items.map((row) => (
              <ConversationSidebarItem
                key={row.id}
                row={row}
                active={row.id === activeId}
                onSelect={onSwitch}
                onDeleteRequest={setPendingDeleteId}
              />
            ))
          )}
        </div>
      </aside>
      <ConfirmDialog
        open={pendingDeleteId !== null}
        title="Delete this conversation?"
        message={
          pendingRow
            ? `"${pendingRow.title ?? "Untitled"}" and its messages will be permanently deleted. This cannot be undone.`
            : "This conversation will be permanently deleted. This cannot be undone."
        }
        confirmLabel="Delete"
        cancelLabel="Cancel"
        destructive
        onConfirm={confirmDelete}
        onCancel={() => setPendingDeleteId(null)}
      />
    </>
  );
}

function SidebarSkeleton() {
  return (
    <div className="px-4 py-2" aria-hidden>
      {[0, 1, 2].map((i) => (
        <div key={i} className="my-2 h-6 animate-pulse rounded bg-paper-dim" />
      ))}
    </div>
  );
}

function SidebarMessage({ children }: { children: string }) {
  return (
    <div className="px-4 py-6 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-ghost">
      {children}
    </div>
  );
}
