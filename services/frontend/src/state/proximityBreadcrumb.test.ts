import { describe, expect, it } from "vitest";

import { proximityBreadcrumb } from "./proximityBreadcrumb";

// Fed the EXACT success strings from chat/tools/proximity.py so a backend reword
// (guarded by tests/unit/test_proximity_tools.py) can't silently break parsing.
describe("proximityBreadcrumb", () => {
  it("parses a straight-line distance result", () => {
    expect(
      proximityBreadcrumb(
        "This apartment is about 9.4 km from FU Berlin (straight-line distance).",
      ),
    ).toBe("9.4 km to FU Berlin");
  });

  it("parses a car travel-time result", () => {
    expect(
      proximityBreadcrumb("Listing #3 is about 19 min from FU Berlin by car."),
    ).toBe("19 min to FU Berlin by car");
  });

  it("maps public transport → transit", () => {
    expect(
      proximityBreadcrumb(
        "This apartment is about 43 min from FU Berlin by public transport.",
      ),
    ).toBe("43 min to FU Berlin by transit");
  });

  it("ignores the trailing stale-schedule note", () => {
    expect(
      proximityBreadcrumb(
        "This apartment is about 43 min from FU Berlin by public transport." +
          " (Transit times reflect the timetable as of 2025-12-14.)",
      ),
    ).toBe("43 min to FU Berlin by transit");
  });

  it("keeps a multi-word place label whole", () => {
    expect(
      proximityBreadcrumb(
        "This apartment is about 1.2 km from the Spree (straight-line distance).",
      ),
    ).toBe("1.2 km to the Spree");
  });

  it("returns '' for non-measurement prose and non-strings", () => {
    for (const s of [
      "No listing is open right now, so I don't know which apartment you mean.",
      "I couldn't measure the distance from this apartment to FU Berlin (its coordinates are missing).",
      "This apartment has no reachable route to FU Berlin by car within the routable window.",
      "I couldn't reach the car routing service to compute the travel time to FU Berlin. Want me to try again in a moment?",
      "",
    ]) {
      expect(proximityBreadcrumb(s)).toBe("");
    }
    expect(proximityBreadcrumb(null)).toBe("");
    expect(proximityBreadcrumb(undefined)).toBe("");
    expect(proximityBreadcrumb(42)).toBe("");
  });
});
