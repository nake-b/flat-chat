import { useEffect, useRef, useState } from "react";

import { useAuth } from "../hooks/useAuth";

// The account dropdown in the header utility bar: shows the signed-in email and,
// on click, a small menu with the email (muted label), Settings (disabled —
// "soon"), and Sign out. No dropdown library is in package.json, so this is
// hand-rolled following ConfirmDialog's Escape-to-close pattern, plus an
// outside-click (mousedown) close. role="menu" / role="menuitem" for a11y.
export function AccountMenu() {
  const email = useAuth((s) => s.user?.email);
  const logout = useAuth((s) => s.logout);
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={email ?? undefined}
        className="flex items-center gap-1.5 px-1.5 py-1 font-mono text-[10px] uppercase tracking-[0.08em] text-ink-soft transition-colors hover:text-red"
      >
        <span className="max-w-[180px] truncate">{email ?? ""}</span>
        <svg
          viewBox="0 0 24 24"
          width="14"
          height="14"
          aria-hidden
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={
            "text-ink-ghost transition-transform " + (open ? "rotate-180" : "")
          }
        >
          <path d="M6 10l6 6 6-6" />
        </svg>
      </button>

      {open ? (
        <div
          role="menu"
          aria-label="Account"
          className="absolute right-0 top-[calc(100%+6px)] z-30 min-w-[210px] border border-paper-rule bg-paper py-1.5 shadow-[0_8px_24px_rgba(0,0,0,0.13)]"
        >
          <div className="truncate px-4 py-2 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-ghost">
            {email ?? ""}
          </div>
          <div className="my-1.5 h-px bg-paper-rule" />
          <div
            role="menuitem"
            aria-disabled="true"
            className="flex cursor-default items-center gap-2 px-4 py-2 font-sans text-[13.5px] text-ink-ghost"
          >
            Settings
            <span className="font-mono text-[9px] tracking-wide opacity-70">
              (soon)
            </span>
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              void logout();
            }}
            className="block w-full px-4 py-2 text-left font-sans text-[13.5px] text-ink transition-colors hover:bg-paper-dim"
          >
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}
