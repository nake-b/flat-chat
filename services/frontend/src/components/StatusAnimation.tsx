import { useEffect, useState } from "react";

// Reusable liveliness primitives for status pills (Claude-Code style). Shared
// by the tool-call pills and the "Thinking" indicator so the animation logic
// lives in exactly one place.

// Trailing dots that cycle "" → "." → ".." → "..." on an interval. Rendered
// after a running label instead of a static "…" so every in-flight pill
// breathes. aria-hidden — the dots are decorative; the label carries meaning.
export function AnimatedDots({ intervalMs = 400 }: { intervalMs?: number }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setN((x) => (x + 1) % 4), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  // Fixed-width slot so the label doesn't jiggle as the dot count changes.
  return (
    <span aria-hidden className="inline-block w-[1.4ch] text-left">
      {".".repeat(n)}
    </span>
  );
}

// Cycles through a list of words, returning the current one. Used for the
// dynamic "Thinking / Reasoning / …" verb and the pagination
// "Checking / Reviewing / Browsing" verb. Pass a STABLE array (a module-level
// const) — a fresh array literal each render would restart the timer.
export function RotatingWord({
  words,
  intervalMs = 1800,
}: {
  words: readonly string[];
  intervalMs?: number;
}) {
  const [i, setI] = useState(0);
  useEffect(() => {
    if (words.length <= 1) return undefined;
    const id = setInterval(() => setI((x) => (x + 1) % words.length), intervalMs);
    return () => clearInterval(id);
  }, [words, intervalMs]);
  return <>{words[i % words.length]}</>;
}
