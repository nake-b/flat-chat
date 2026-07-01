import { describe, expect, it } from "vitest";

import { TOOL_STATUS } from "./toolStatus";

// The tool-finish copy is the render SSOT (issue #22): the search completion
// renders "Found N apartments" / "No apartments found …" parsed from the backend
// summary, both live and on reload (it's a real tool result in the transcript).
// Multi-search stacking is prevented on the BACKEND (only the last result per
// turn keeps content), not by silencing here.
describe("TOOL_STATUS.search_apartments", () => {
  const spec = TOOL_STATUS.search_apartments;

  it("renders the count as the completion finish", () => {
    expect(
      spec.complete!({}, "Found 48 listings, most recent first.\nShowing 1–5", null),
    ).toBe("Found 48 apartments");
    expect(spec.complete!({}, "Found 1 listings, most recent first.", null)).toBe(
      "Found 1 apartment",
    );
  });

  it("renders the no-results finish", () => {
    expect(
      spec.complete!(
        {},
        "No apartments found matching those criteria. Try broadening your search.",
        null,
      ),
    ).toBe("No apartments found — try broadening your search");
  });

  it("renders nothing for an unparseable / blanked result", () => {
    // Backend blanks superseded intermediate searches to "" → no pill.
    expect(spec.complete!({}, "", null)).toBe("");
    expect(spec.complete!({}, "some other text", null)).toBe("");
  });

  it("shows a live executing label", () => {
    expect(spec.executing({ districts: ["Kreuzberg"] })).toBe("Searching Kreuzberg");
    expect(spec.executing({})).toBe("Searching apartments");
  });
});

describe("TOOL_STATUS.get_result_page", () => {
  it("has a silent completion (cards/map carry the result)", () => {
    expect(TOOL_STATUS.get_result_page.complete!({}, "Page 2/5…", null)).toBe("");
  });
});

describe("TOOL_STATUS proximity tools", () => {
  it("distance_to: generic running label, silent finish (prose is the answer)", () => {
    expect(TOOL_STATUS.distance_to.executing({})).toBe("Measuring distance");
    expect(TOOL_STATUS.distance_to.complete!({}, "≈ 4.2 km …", null)).toBe("");
  });

  it("travel_time_to: running label reflects mode, silent finish", () => {
    expect(TOOL_STATUS.travel_time_to.executing({ mode: "car" })).toBe(
      "Checking drive time",
    );
    expect(TOOL_STATUS.travel_time_to.executing({ mode: "transit" })).toBe(
      "Checking travel time",
    );
    expect(TOOL_STATUS.travel_time_to.executing({})).toBe("Checking travel time");
    expect(TOOL_STATUS.travel_time_to.complete!({}, "≈ 18 min …", null)).toBe("");
  });
});
