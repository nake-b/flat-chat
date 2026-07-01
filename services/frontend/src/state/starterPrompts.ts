// Empty-state starter prompts — the discoverability affordance shown before the
// first message (NN/g "prompt controls"). Curated, capability-tagged, and sampled
// one-per-category so the three cards showcase DIFFERENT things the app can do
// (not three near-duplicates).
//
// Each prompt is a concrete object:
//   - `emoji`   → shown inline before the label on the card
//   - `label`   → short chip text shown on the card (keeps it compact)
//   - `prompt`  → the full sentence actually sent to the agent (sendMessage)
//   - `category`→ capability bucket, drives the stratified sampler below
//
// Curation rule: every prompt maps to a REAL, STRUCTURED capability. We do NOT
// advertise soft attributes we can't actually filter (e.g. "dog-friendly",
// "student-friendly") — even though the agent handles them gracefully via the
// free-text `query` semantic fallback (chat/agent.py `_semantic_fallback_block`),
// we don't want a starter card *encouraging* users to ask for something we
// didn't implement. That honesty policy stays for user-typed queries only.

export type StarterCategory =
  | "budget"
  | "place"
  | "transit"
  | "family"
  | "nature"
  | "calm"
  | "map"
  | "health";

export interface StarterPrompt {
  category: StarterCategory;
  emoji: string;
  label: string;
  prompt: string;
}

export const STARTER_HEADLINES = [
  "Example prompts to get you started:",
  "Not sure where to begin? Try one:",
  "A few examples to try:",
] as const;

export const STARTER_PROMPTS: StarterPrompt[] = [
  // budget — price / size / amenities
  {
    category: "budget",
    emoji: "🏡",
    label: "A 2-room with a balcony, somewhere quiet and green",
    prompt:
      "🏡 I am looking for a 2 rooms apartment for up to 1200€ with a balcony. It would ideally be located in a quiet and green area.",
  },
  {
    category: "budget",
    emoji: "💶",
    label: "The cheapest places inside the S-Bahn ring",
    prompt:
      "💶 What's the cheapest place you can find inside the S-Bahn ring? Show them on the map.",
  },
  // place — proximity to a specific named place (locate_place)
  {
    category: "place",
    emoji: "📍",
    label: "Everything within 500 m of Alexanderplatz",
    prompt:
      "📍 Show me all flats within 500 m of Alexanderplatz. Price and size don't matter.",
  },
  {
    category: "place",
    emoji: "🎶",
    label: "Apartments near the Uber Arena",
    prompt: "🎶 Show me apartments within 2 km of the Uber Arena.",
  },
  {
    category: "place",
    emoji: "🚴",
    label: "Within biking distance of FU Berlin",
    prompt:
      "🚴 Find me a potential new home within biking distance of Freie Universität Berlin.",
  },
  // transit
  {
    category: "transit",
    emoji: "🚇",
    label: "Flats right along the U7 line",
    prompt: "🚇 What flats do you have along the U7?",
  },
  {
    category: "transit",
    emoji: "🚊",
    label: "Near a tram or bus, just a short walk",
    prompt:
      "I want apartments near a tram or bus stop, within a very short walk.",
  },
  // family — kita / playground / school
  {
    category: "family",
    emoji: "👨‍👩‍👧‍👦",
    label: "Child-friendly, with a playground nearby",
    prompt:
      "👨‍👩‍👧‍👦 Find a 2-3 room apartment in Pankow or Reinickendorf under 1500€. It should be child friendly, with a playground nearby.",
  },
  {
    category: "family",
    emoji: "🎒",
    label: "Close to both a Kita and a Grundschule",
    prompt:
      "Find me listings close to a Kita and a Grundschule at the same time.",
  },
  // nature — park / water / greenery
  {
    category: "nature",
    emoji: "🌊",
    label: "Close to a lake",
    prompt: "🌊 Find me a place close to a lake.",
  },
  {
    category: "nature",
    emoji: "🌳",
    label: "Right next to a park",
    prompt: "🌳 Find apartments right next to a park.",
  },
  // calm — quiet / low-density
  {
    category: "calm",
    emoji: "👶",
    label: "A family home in a calm, leafy area",
    prompt:
      "Find a 2-3 bedroom home for a future family 👶 in a low-populated area with lots of greenery.",
  },
  {
    category: "calm",
    emoji: "🤫",
    label: "Quiet streets, still close to parks",
    prompt: "Which flats are in quieter areas 🤫 and still close to parks?",
  },
  {
    category: "calm",
    emoji: "🌾",
    label: "A calm, low-populated neighbourhood",
    prompt:
      "🌾 I'm looking for a new place in a low-populated area. 1-2 rooms would be ideal; price doesn't matter.",
  },
  // map / ring — inside-vs-outside the S-Bahn ring
  {
    category: "map",
    emoji: "🗺️",
    label: "Inside the ring, under €1,500",
    prompt: "🗺️ Show me flats inside the S-Bahn ring, under €1500 a month.",
  },
  {
    category: "map",
    emoji: "💧",
    label: "Outside the ring, but close to water",
    prompt:
      "Show me places outside the S-Bahn ring but still close to water.",
  },
  // health — hospitals
  {
    category: "health",
    emoji: "🏥",
    label: "A hospital within walking distance",
    prompt:
      "🏥 Show me apartments with a hospital within walking distance.",
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
