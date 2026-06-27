import type { MouseEvent } from "react";

interface Props {
  filled: boolean;
  onToggle: () => void;
  // Visual size variant — card uses sm, detail header uses md.
  size?: "sm" | "md";
  // Used in the aria-label so screen readers know which listing is being saved.
  label?: string;
}

// Clickable star button. Yellow-filled when bookmarked, outline grey otherwise.
// stopPropagation so this button can sit OVER a parent <button> (the apartment
// card) — nested buttons are invalid HTML, so the star is a sibling absolutely
// positioned over the corner, and the propagation guard means a star click
// doesn't also activate the card.
export function BookmarkStar({
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
      data-testid="bookmark-star"
      className={
        "flex items-center justify-center transition-colors " +
        (filled
          ? "text-amber-400 hover:text-amber-500"
          : "text-ink-ghost hover:text-amber-400")
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
        <polygon points="12 2 14.9 8.6 22 9.3 16.5 14.1 18.2 21 12 17.3 5.8 21 7.5 14.1 2 9.3 9.1 8.6 12 2" />
      </svg>
    </button>
  );
}
