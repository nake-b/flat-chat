import { ChatPane } from "./components/ChatPane";
import { MapPane } from "./components/MapPane";
import { CardsPane } from "./components/CardsPane";

// Chat-host layout: chat left ~40%, map+cards artifact right ~60%.
// Desktop-only — CLAUDE.md "Out of Scope" lists mobile as deferred.
//
// Option-X (cards section grew when a card was active) is removed: user
// kept complaining about the dead whitespace left below the compact
// CardDetail in the expanded slot. Clicking a card now just swaps the
// strip for the detail in the SAME slot height — same visual density,
// no jarring layout shift. The bet was that the slot growth would help
// users focus on the detail; in practice it just exposed wasted space.
const TOP_PCT = 70;

function App() {
  return (
    <div className="grid h-screen w-screen grid-cols-[2fr_3fr] overflow-hidden bg-paper">
      <aside className="overflow-hidden border-r border-paper-rule">
        <ChatPane />
      </aside>
      <main className="relative h-full overflow-hidden bg-paper">
        <section
          className="absolute inset-x-0 top-0 overflow-hidden border-b border-paper-rule"
          style={{ height: `${TOP_PCT}%` }}
        >
          <MapPane />
        </section>
        <section
          className="absolute inset-x-0 bottom-0 overflow-hidden"
          style={{ height: `${100 - TOP_PCT}%` }}
        >
          <CardsPane />
        </section>
      </main>
    </div>
  );
}

export default App;
