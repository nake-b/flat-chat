import type { MouseEvent } from "react";

interface Props {
  filled: boolean;
  onToggle: () => void;
  // Visual size variant — card uses sm, detail header uses md.
  size?: "sm" | "md";
  // Used in the aria-label so screen readers know which listing is being saved.
  label?: string;
}

// Clickable heart button — the save affordance. Red-filled when bookmarked,
// outline grey otherwise. Heart (not star) matches the convention in
// property-search apps (Zillow / ImmoScout / Airbnb) where a star reads as a
// rating; see agent-compound-docs/decisions/bookmark-affordance.md.
//
// stopPropagation so this button can sit OVER a parent <button> (the apartment
// card) — nested buttons are invalid HTML, so the heart is a sibling absolutely
// positioned over the corner, and the propagation guard means a heart click
// doesn't also activate the card.
export function BookmarkHeart({
  filled,
  onToggle,
  size = "sm",
  label,
}: Props) {
  const px = size === "md" ? 22 : 18;
  const handleClick = (e: MouseEvent<HTMLButtonElement>) => {
    e.preventDefault();
    e.stopPropagation();
    onToggle();
  };
  const ariaLabel = filled
    ? label
      ? `Remove bookmark: ${label}`
      : "Remove bookmark"
    : label
      ? `Bookmark: ${label}`
      : "Add bookmark";
  return (
    <button
      type="button"
      onClick={handleClick}
      aria-label={ariaLabel}
      aria-pressed={filled}
      data-testid="bookmark-heart"
      className={
        "flex items-center justify-center transition-colors " +
        (filled
          ? "text-red hover:text-red-deep"
          : "text-ink-ghost hover:text-red")
      }
    >
      <svg
        viewBox="0 0 24 24"
        width={px}
        height={px}
        aria-hidden
        fill={filled ? "currentColor" : "none"}
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z" />
      </svg>
    </button>
  );
}
