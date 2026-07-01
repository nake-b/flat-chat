import { describe, expect, it } from "vitest";

import {
  STARTER_PROMPTS,
  pickStratified,
  type StarterPrompt,
} from "./starterPrompts";

describe("pickStratified", () => {
  it("returns the requested count", () => {
    for (let i = 0; i < 50; i += 1) {
      expect(pickStratified(STARTER_PROMPTS, 3)).toHaveLength(3);
    }
  });

  it("picks from distinct capability categories", () => {
    for (let i = 0; i < 50; i += 1) {
      const chosen = pickStratified(STARTER_PROMPTS, 3);
      const categories = chosen.map((p) => p.category);
      expect(new Set(categories).size).toBe(categories.length);
    }
  });

  it("only returns prompts from the pool (no fabrication)", () => {
    const chosen = pickStratified(STARTER_PROMPTS, 3);
    for (const p of chosen) {
      expect(STARTER_PROMPTS).toContain(p);
    }
  });

  it("fills from leftovers when categories are fewer than count", () => {
    // Two categories, ask for 3 → must still return 3 distinct prompts.
    const pool: StarterPrompt[] = [
      { category: "budget", label: "a", prompt: "a" },
      { category: "budget", label: "b", prompt: "b" },
      { category: "transit", label: "c", prompt: "c" },
    ];
    const chosen = pickStratified(pool, 3);
    expect(chosen).toHaveLength(3);
    expect(new Set(chosen.map((p) => p.label)).size).toBe(3);
  });

  it("does not exceed the pool size", () => {
    const pool: StarterPrompt[] = [
      { category: "budget", label: "a", prompt: "a" },
      { category: "transit", label: "b", prompt: "b" },
    ];
    expect(pickStratified(pool, 5)).toHaveLength(2);
  });
});
