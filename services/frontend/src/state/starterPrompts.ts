// Empty-state starter prompts — the discoverability affordance shown before the
// first message (NN/g "prompt controls"). Curated, capability-tagged, and sampled
// one-per-category so the three pills showcase DIFFERENT things the app can do
// (not three near-duplicates).
//
// Each prompt is a concrete object:
//   - `label`   → short chip text shown on the pill (keeps the pill compact)
//   - `prompt`  → the full sentence actually sent to the agent (sendMessage)
//   - `category`→ capability bucket, drives the stratified sampler below
//
// Curation rule: every prompt must map to a REAL capability. "Soft" attributes
// with no structured filter (student-friendly, dog vibe) route to the agent's
// free-text `query` (semantic ranking) — the agent is instructed to say so
// (see chat/agent.py `_semantic_fallback_block`). Kept intentionally as the
// `semantic`/`nature` demos, not as false advertising.

export type StarterCategory =
  | "budget"
  | "place"
  | "transit"
  | "family"
  | "nature"
  | "calm"
  | "map"
  | "health"
  | "semantic";

export interface StarterPrompt {
  category: StarterCategory;
  label: string;
  prompt: string;
}

export const STARTER_HEADLINES = [
  "New here? Ask me something:",
  "A few examples of what I can do:",
  "What can I do for you? Ask:",
] as const;

export const STARTER_PROMPTS: StarterPrompt[] = [
  // budget — price / size / amenities
  {
    category: "budget",
    label: "2-room · balcony · quiet & green",
    prompt:
      "🏡 I am looking for a 2 rooms apartment for up to 1200€ with a balcony. It would ideally be located in a quiet and green area.",
  },
  {
    category: "budget",
    label: "Cheapest inside the ring",
    prompt:
      "💶 What's the cheapest place you can find inside the S-Bahn ring? Show them on the map.",
  },
  // place — proximity to a specific named place (locate_place)
  {
    category: "place",
    label: "500 m around Alexanderplatz",
    prompt:
      "📍 Show me all flats within 500 m of Alexanderplatz. Price and size don't matter.",
  },
  {
    category: "place",
    label: "2 km around Uber Arena",
    prompt: "🎶 Show me apartments within 2 km of the Uber Arena.",
  },
  {
    category: "place",
    label: "Biking distance to FU Berlin",
    prompt:
      "🚴 Find me a potential new home within biking distance of Freie Universität Berlin.",
  },
  // transit
  {
    category: "transit",
    label: "Along the U7",
    prompt: "🚇 What flats do you have along the U7?",
  },
  {
    category: "transit",
    label: "Near a tram or bus, short walk",
    prompt:
      "I want apartments near a tram or bus stop, within a very short walk.",
  },
  // family — kita / playground / school
  {
    category: "family",
    label: "Child-friendly, playground nearby",
    prompt:
      "👨‍👩‍👧‍👦 Find a 2-3 room apartment in Pankow or Reinickendorf under 1500€. It should be child friendly, with a playground nearby.",
  },
  {
    category: "family",
    label: "Near a Kita and a Grundschule",
    prompt:
      "Find me listings close to a Kita and a Grundschule at the same time.",
  },
  // nature — park / water / greenery
  {
    category: "nature",
    label: "Near a big park (moving with a dog)",
    prompt:
      "🐶 We're moving with a dog. Find dog-friendly apartments near a large park, 2-3 rooms, up to 1800€.",
  },
  {
    category: "nature",
    label: "Close to a lake",
    prompt: "🌊 Find me a place close to a lake.",
  },
  {
    category: "nature",
    label: "Next to a big park",
    prompt: "🌳 Find apartments right next to a big park.",
  },
  // calm — quiet / low-density
  {
    category: "calm",
    label: "Low-populated, lots of greenery",
    prompt:
      "Find a 2-3 bedroom home for a future family 👶 in a low-populated area with lots of greenery.",
  },
  {
    category: "calm",
    label: "Quiet area near parks",
    prompt: "Which flats are in quieter areas 🤫 and still close to parks?",
  },
  {
    category: "calm",
    label: "Low-populated, 1–2 rooms",
    prompt:
      "🌾 I'm looking for a new place in a low-populated area. 1-2 rooms would be ideal; price doesn't matter.",
  },
  // map — overlays / ring visualization
  {
    category: "map",
    label: "Everything around the S-Bahn Ring",
    prompt: "🗺️ Please visualise all available apartments around the S-Bahn Ring.",
  },
  {
    category: "map",
    label: "Outside the ring, near water",
    prompt:
      "Show me places outside the S-Bahn ring but still close to water.",
  },
  // health — hospitals
  {
    category: "health",
    label: "Hospital nearby, compare distances",
    prompt:
      "🏥 Show me apartments with a hospital nearby and compare how far the closest hospitals are.",
  },
  // semantic — soft attribute → free-text query (agent warns it can't hard-filter)
  {
    category: "semantic",
    label: "Student-friendly + which buses",
    prompt:
      "🎓 Find me a student-friendly apartment in Steglitz-Zehlendorf. And which buses stop there?",
  },
];

function shuffle<T>(items: readonly T[]): T[] {
  const out = [...items];
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/** Pick a random single item (e.g. the headline). */
export function pickRandom<T>(items: readonly T[]): T {
  return shuffle(items)[0];
}

/**
 * Pick `count` prompts from DISTINCT capability categories so the visible set
 * showcases different things the app can do. Shuffles the category list, takes
 * one random prompt per category; if there are fewer categories than `count`,
 * fills the remainder from the leftover pool (still avoiding duplicates).
 */
export function pickStratified(
  items: readonly StarterPrompt[],
  count: number,
): StarterPrompt[] {
  const byCategory = new Map<StarterCategory, StarterPrompt[]>();
  for (const item of items) {
    const bucket = byCategory.get(item.category);
    if (bucket) bucket.push(item);
    else byCategory.set(item.category, [item]);
  }

  const chosen: StarterPrompt[] = [];
  for (const category of shuffle([...byCategory.keys()])) {
    if (chosen.length >= count) break;
    chosen.push(pickRandom(byCategory.get(category)!));
  }

  if (chosen.length < count) {
    const leftovers = shuffle(items.filter((i) => !chosen.includes(i)));
    chosen.push(...leftovers.slice(0, count - chosen.length));
  }

  return chosen.slice(0, count);
}
