import { describe, expect, it } from "vitest";

import {
  formatTransitCompact,
  formatTransitDetailed,
  transitMode,
} from "./transit";

describe("transitMode", () => {
  it("classifies by line prefix, mirroring the backend heuristic", () => {
    expect(transitMode("U7")).toBe("u_bahn");
    expect(transitMode("S41")).toBe("s_bahn");
    expect(transitMode("M10")).toBe("tram");
    expect(transitMode("N7")).toBe("night");
    expect(transitMode("245")).toBe("bus");
    expect(transitMode("X9")).toBe("bus");
    // Numeric tram (12/16) reads as "bus" — cosmetic only.
    expect(transitMode("12")).toBe("bus");
  });

  it("defaults to bus on an empty label", () => {
    expect(transitMode("")).toBe("bus");
  });
});

describe("formatTransitCompact", () => {
  it("renders '<icon> <line> · <n>min' for the result cards", () => {
    expect(formatTransitCompact("U7", 3)).toBe("🚇 U7 · 3min");
    expect(formatTransitCompact("S1", 5)).toBe("🚆 S1 · 5min");
  });
});

describe("formatTransitDetailed", () => {
  it("renders '<n> min walk to <icon> <line>' for the bookmark rows", () => {
    // Contract locked by BookmarkSidebar.test.tsx.
    expect(formatTransitDetailed("U7", 8)).toBe("8 min walk to 🚇 U7");
    expect(formatTransitDetailed("M10", 2)).toBe("2 min walk to 🚊 M10");
  });
});
