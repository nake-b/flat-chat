import { describe, expect, it } from "vitest";

import { formatSearchBreadcrumb, parseSearchCount } from "./searchBreadcrumb";

// These pin the cross-language contract with the backend summary
// (chat/llm_context.py): the count the breadcrumb shows is parsed from the
// search tool's result string. See test_llm_context.py for the backend side.
describe("parseSearchCount", () => {
  it("parses a positive count from the summary headline", () => {
    expect(
      parseSearchCount("Found 48 listings, most recent first.\nShowing 1–5:\n  1. …"),
    ).toBe(48);
  });
  it("parses the singular form", () => {
    expect(parseSearchCount("Found 1 listings, most recent first.")).toBe(1);
  });
  it("parses the zero-results prose to 0", () => {
    expect(
      parseSearchCount(
        "No apartments found matching those criteria. Try broadening your search.",
      ),
    ).toBe(0);
  });
  it("strips thousands separators", () => {
    expect(parseSearchCount("Found 1,234 listings, most recent first.")).toBe(1234);
  });
  it("returns null for unrelated strings / non-strings", () => {
    expect(parseSearchCount("Opened listing #3")).toBeNull();
    expect(parseSearchCount("Page 2/5 — listings 11–20 of 48")).toBeNull();
    expect(parseSearchCount(undefined)).toBeNull();
    expect(parseSearchCount(null)).toBeNull();
  });
});

describe("formatSearchBreadcrumb", () => {
  it("pluralizes hits", () => {
    expect(formatSearchBreadcrumb(48)).toBe("Found 48 apartments");
    expect(formatSearchBreadcrumb(1)).toBe("Found 1 apartment");
  });
  it("uses broaden copy for zero", () => {
    expect(formatSearchBreadcrumb(0)).toBe(
      "No apartments found — try broadening your search",
    );
  });
});
