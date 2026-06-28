/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Berliner Rot — the city's official red (Senatsverwaltung flag).
        // Used as the singular accent; never decorative, only load-bearing.
        red: {
          DEFAULT: "#E4003C",
          deep: "#B00030",
          tint: "#FBE9EE",
        },
        ink: {
          DEFAULT: "#0A0A0A",
          soft: "#2A2A2A",
          ghost: "#8A8580",
        },
        paper: {
          DEFAULT: "#FAFAF7",
          dim: "#F2F0EA",
          rule: "#E0DCD2",
        },
      },
      fontFamily: {
        // Display: Fraunces — variable serif with optical-size axis, gives
        // the "Berlin signage / government brochure" gravitas to wordmark
        // and section headers.
        display: ['"Fraunces"', "ui-serif", "Georgia", "serif"],
        // UI: Bricolage Grotesque — humanist grotesque, civic but warmer
        // than Helvetica. Used for body, chat, controls.
        sans: ['"Bricolage Grotesque"', "ui-sans-serif", "system-ui", "sans-serif"],
        // Numeric: JetBrains Mono — anchors prices, areas, addresses with
        // a typewriter precision that pairs well with the civic aesthetic.
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
      letterSpacing: {
        tightest: "-0.04em",
      },
      transitionTimingFunction: {
        snap: "cubic-bezier(0.22, 0.61, 0.36, 1)",
      },
      keyframes: {
        // Detail-panel entrance — content eases up + fades in as the bottom
        // panel reveals. Re-triggered per listing via a `key={activeId}` remount.
        "detail-rise": {
          "0%": { opacity: "0", transform: "translateY(16px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "detail-rise":
          "detail-rise 360ms cubic-bezier(0.22, 0.61, 0.36, 1) both",
      },
},
  },
  plugins: [],
};
