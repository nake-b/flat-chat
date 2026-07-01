import { useEffect, useRef } from "react";

interface Props {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

// Centered modal for destructive confirms (delete a conversation today;
// reusable for rename collisions, etc., later). Unlike the sidebar
// (role="complementary"), this IS a blocking modal — role="dialog" +
// aria-modal="true" + focus moves to the confirm button on open.
//
// Esc + backdrop click cancel. The confirm button receives focus so Enter
// confirms — common pattern for delete dialogs ("press Enter to confirm").
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Delete",
  cancelLabel = "Cancel",
  destructive = true,
  onConfirm,
  onCancel,
}: Props) {
  const confirmRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    confirmRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  const confirmClasses = destructive
    ? "border-red bg-red text-paper hover:bg-red-deep"
    : "border-ink bg-ink text-paper hover:bg-ink-soft";

  return (
    <>
      <button
        type="button"
        aria-label="Dismiss dialog"
        tabIndex={-1}
        onClick={onCancel}
        data-testid="confirm-dialog-backdrop"
        className="fixed inset-0 z-[60] cursor-default bg-ink/50 transition-opacity duration-150"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        className="fixed left-1/2 top-1/2 z-[70] w-[400px] -translate-x-1/2 -translate-y-1/2 border border-paper-rule bg-paper p-6 shadow-2xl"
      >
        <h2
          id="confirm-dialog-title"
          className="font-display text-xl font-semibold leading-tight tracking-tight text-ink"
        >
          {title}
        </h2>
        <p className="mt-3 font-sans text-sm text-ink-soft">{message}</p>
        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            className="border border-paper-rule px-4 py-2 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-soft transition-colors hover:bg-paper-dim hover:text-ink"
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            type="button"
            onClick={onConfirm}
            className={
              "border px-4 py-2 font-mono text-[10px] uppercase tracking-[0.14em] transition-colors " +
              confirmClasses
            }
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </>
  );
}
