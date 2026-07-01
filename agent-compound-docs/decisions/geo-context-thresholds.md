# Geo-context interpretation thresholds — empirical audit trail

Decided 2026-06-14 while designing the integration of the geo-context silver tables into the search module and agent tooling.

> **June 2026 update.** Files moved during the search-perf refactor:
> - `search/distances.py` + `search/buckets.py` + `search/transit.py` →
>   merged into [`listings/labels.py`](../../services/backend/src/flat_chat/listings/labels.py)
>   + [`listings/thresholds.py`](../../services/backend/src/flat_chat/listings/thresholds.py).
> - MSS German→English maps moved out of the backend entirely (search-perf refactor).
> Values unchanged — only file locations moved.

> **geo-context v2 update (June 2026): MSS / Sozialmonitoring was removed
> entirely** on ethical grounds — the source table, the `mss_*` columns, the
> filter, and the German→English label map are all gone. **Section 8 below is
> retained as a historical record of the removed decision, not a live
> reference.** See [`named-place-search.md`](named-place-search.md) and
> [`bezirk-ortsteil-resolution.md`](bezirk-ortsteil-resolution.md).

This doc is the **audit trail for every numeric constant and label choice** used to interpret geo-context data — "what does *near* mean", "what counts as a *quiet* street". Every constant in `listings/thresholds.py` + `listings/labels.py` traces to a row in this doc.

**Rule**: constants without a row in this doc are technical debt. Doc-first, code-second.

**The LLM tool docs must match these constants.** The distance ladder / noise / greenery / density cutoffs are written out literally in the `search_apartments` docstring + phrase map (`chat/tools.py`). When you tune a constant here, update that prose too — `test_search_tool_docs_match_thresholds` reads these constants and asserts each appears in the right parameter description, so drift fails CI loudly.

Each section has the chosen value, the original research/spec value that informed it, the source URL, and (where applicable) a "Berlin delta" column explaining why we adjusted from the canonical number.

---

## 1. Walking distance ladder

Used by `NearSpec` enum values across all "near X" filters (`near_school`, `near_park`, `transit.distance`, …).

| Bucket | Chosen (m) | Original research value (m) | Source | Berlin-delta rationale |
|---|---|---|---|---|
| `next_to` | **150** | 100 | CNU pedestrian shed | Berlin block spacing > Tokyo / dense US grid; 100m too strict for "right next to" in practice |
| `very_near` | **400** | 300 | EU END / WHO walkability | Aligns with the canonical 5-min walk @ 1.4 m/s |
| `near` *(default)* | **650** | 500 | DWDS "fußläufig erreichbar" (German urban planning term) | Berlin's lower-density inner ring needs more headroom; 500m too tight |
| `walking_distance` | **1200** | 1000 | Calthorpe TOD (Transit-Oriented Development) | "Fußläufig" extended for outer districts |
| `bike_distance` | **2500** | 2000 | (own extension — no canonical source) | Pragmatic upper bound; ~15 min walk or ~8 min bike |

Sources:
- https://morphocode.com/the-5-minute-walk/ — the canonical "pedestrian shed" article
- https://www.cnu.org/publicsquare/2021/02/08/defining-15-minute-city — 15-min-city literature
- https://www.dwds.de/wb/fußläufig — German urban-planning definition of "fußläufig erreichbar"
- https://en.wikipedia.org/wiki/Transit-oriented_development — Calthorpe TOD ranges

**Per-dataset hard caps** (used by the `k=1-always, k=2..k within cap` rule in `GeoContextService._nearest_*` helpers):

| Dataset | Cap (m) | Reasoning |
|---|---|---|
| schools | 2500 | urban context; further is irrelevant |
| parks | 1500 | "would you walk there?" |
| playgrounds | 1000 | parents won't push strollers beyond ~10 min for play |
| hospitals | 5000 | medical context, larger range acceptable |
| water_bodies | 2000 | scenic amenity, generous cap |
| transit_stops | 1500 | anything beyond is not "your stop" |

**Gold-layer storage radii** (`R_NEARBY_*_M` in `services/ingestion/src/gold/enrich_listings.py`) — generous on purpose; search-time predicates do the actual cutoff. The two added for geo-context v2:

| Constant | Value (m) | Rationale |
|---|---|---|
| `R_NEARBY_KITAS_M` | **3000** | Kitas are denser + more hyperlocal than schools (a family wants the *nearest* day-care, not one across town) → mirror playgrounds' 3 km, not schools' 5 km. |
| `R_NEARBY_LANDMARKS_M` | **2000** | Notable landmarks (monuments, towers, bridges, stadiums, attractions) are sparse; "near a landmark" is a generous, low-frequency relationship — 2 km keeps the junction populated without flooding it. |

---

## 2. Pedestrian walking speed

| Constant | Chosen | Source |
|---|---|---|
| `_PEDESTRIAN_M_PER_S` | **1.4** | https://en.wikipedia.org/wiki/Walking — adult average ~5 km/h ≈ 1.4 m/s. Used by EAÖ German transit-planning standards. Google Maps uses ~1.34 m/s (3 mph), slightly more conservative; we picked the more common 1.4. |

Used by `walk_minutes(meters)` to convert distances to walk times for UI chips ("🚇 U8 · 4min").

| Constant | Chosen | Source |
|---|---|---|
| `CAP_LAST_MILE_WALK_M` | **1500** | Last-mile stop→listing walk cap for the transit travel-time lens: a listing's transit time = min over stops within this range of (anchor→stop + walk(stop→listing)). Matches `CAP_TRANSIT_STOPS_M` (both mean "a stop is 'near' a listing at ≤1.5 km" ≈ 18 min at 1.4 m/s); Berliners routinely walk >1 km to a station. **Routing-only** — NOT part of the gold ETL, so (unlike the caps in §1) it is not duplicated into ingestion. Used by `routing/service.py`. |

**Candidate future addition**: `accessibility=True` mode that bumps speed down to 1.0 m/s for older/mobility-limited renters. Logged here, not v1.

---

## 3. Noise (Lden, dB)

Used by `bucket_noise(lden_dB)` → `Literal["quiet","lively","noisy"]`.

| Bucket | Cutoff | Source |
|---|---|---|
| `quiet` | Lden **< 55 dB** | WHO 2018 — adverse health effects begin at ~53 dB. EU END "exposed" threshold ≥ 55. |
| `lively` | Lden **55–65 dB** | EU END normal urban band; what most Berliners live in |
| `noisy` | Lden **≥ 65 dB** | EU END "high exposure" — meaningful sleep + cognitive impact |
| *(internal flag, not user-facing)* | Lden **≥ 75 dB** | EU END "very high" — informs detail enrichment, not the bucket label |

**Decision**: dropped the `int` escape hatch. Renters don't say "below 53.2 dB". Bucket enum only.

Sources:
- https://www.wbm.co.uk/wp-content/uploads/2018/11/WBM-WHO-2018-Summary-Nov-2018.pdf — WHO 2018 Environmental Noise Guidelines summary
- https://www.eea.europa.eu/en/analysis/indicators/exposure-of-europe-population-to-noise — EU END thresholds
- https://www.berlin.de/umweltatlas/verkehr-laerm/laermbelastung/2022/karten/ — Berlin Umweltatlas noise map (the data source)

**Why absolute (not Berlin-relative)**: a quiet street is < 55 dB *everywhere in the world*, not relative to local norms. WHO and EU did the work; we use their numbers.

---

## 4. Greenery (WHO Europe rule)

Used by `bucket_greenery(green_area_within_300m_m2)` → `Literal["concrete","leafy","very_leafy"]`.

| Bucket | Cutoff | Source |
|---|---|---|
| `concrete` | < 0.5 ha green within 300m | falls below WHO Europe rule |
| `leafy` | **≥ 0.5 ha green within 300m linear distance** | WHO Regional Office for Europe |
| `very_leafy` | ≥ 1 ha green within 300m OR ≥ 0.5 ha within 150m | doubled WHO rule + 3-30-300 tighter standard |

Sources:
- https://www.sciencedirect.com/science/article/pii/S1470160X24010057 — critical review of WHO green-space guidelines
- https://isglobalranking.org/faq-items/what-are-the-who-guidelines-on-green-space/ — ISGlobal summary
- The "3-30-300" rule: 3 visible trees from home, 30% canopy cover, 300m to park

**Why absolute (not Berlin-median)**: Berlin median includes Wannsee, Tegeler Forst, the Müggelsee shoreline — a useless baseline. The WHO rule maps to lived experience.

---

## 5. Cemeteries (Friedhöfe) as green amenity

**Decision**: included in green-amenity composite at **0.5 weight**, but NEVER shown as the `nearest_park` chip on a result card.

Rationale (intentional middle ground — research and user gut disagreed):
- Berlin Senate (SenUVK) officially classes Friedhöfe as publicly-accessible recreation space alongside parks and forests. Berliners genuinely use them as parks: Alter St.-Matthäus-Kirchhof has a café, Dorotheenstädtischer is a tourist walk, Jüdischer Friedhof Weißensee is a major park-like space.
- But some renters find them gloomy. A card chip reading "🌳 Jüdischer Friedhof 200m" is misleading on first glance.
- Compromise: count them at half weight in green-amenity calculations (so they don't disappear from the data), exclude from the named-nearest-park chip (so the UI doesn't surprise the user).

Source:
- https://www.berlin.de/sen/uvk/natur-und-gruen/stadtgruen/friedhoefe-und-begraebnisstaetten/ — Senate policy on Friedhöfe as green space

**Implementation**: in `_nearest_parks(loc, k)`, filter `parks.object_type` out of any cemetery-typed rows. In greenery composite calculations, sum `cemetery_area * 0.5 + park_area * 1.0 + playground_area * 1.0` within radius.

---

## 6. Population density (persons per hectare)

Used by `bucket_density(persons_per_ha)` → `Literal["sparse","moderate","dense"]`.

| Bucket | Cutoff | Source |
|---|---|---|
| `sparse` | **< 50 persons/ha** | general urban-planning suburban density |
| `moderate` | **50–150 persons/ha** | typical urban European density |
| `dense` | **≥ 150 persons/ha** | dense inner-city European norm (Kreuzberg, Neukölln) |

Lower-confidence than other thresholds — no single canonical source. Revisit if user testing surfaces edge cases (e.g. "I asked for moderate but got Friedrichshain"). Data source is `population_density_2025.population_per_hectare` per LOR.

---

## 7. Transit — distances and mode labels

### Distance thresholds for "well-connected"

| Quality | Distance | Source |
|---|---|---|
| well-connected — rail (U/S-Bahn) | **≤ 800 m** | Calthorpe TOD; BC legislates 400 m bus / 800 m rail |
| well-connected — bus/tram | **≤ 400 m** | TOD literature; VDV (Verband Deutscher Verkehrsunternehmen) |

These are *advisory* — the actual tool defaults are the `NearSpec` ladder values (`near=650m` default for `transit.distance`).

### GTFS mode labels (Extended Route Types)

The `transit_stops.modes_served` column stores integer GTFS Extended codes. Tool surface uses English string enums; `search/transit.py:resolve_modes()` maps between them.

| GTFS code | German | English label (user-facing) | Tool enum value |
|---|---|---|---|
| 100 | Eisenbahn (Fernverkehr) | Mainline | `mainline` |
| 106 | Regionalzug | Regional | `regional` |
| 109 | S-Bahn | S-Bahn | `s_bahn` |
| 400 | U-Bahn | U-Bahn | `u_bahn` |
| 700 | Bus | Bus | `bus` |
| 900 | Tram | Tram | `tram` |
| 1000 | Ferry | Ferry | `ferry` |

Sources:
- https://gtfs.org/documentation/schedule/reference/#routestxt — GTFS Extended Route Types
- VBB feed: https://www.vbb.de/vbbgtfs — Berlin's actual published feed (uses Extended codes)

**Why English in the tool, ints in the DB**: LLMs handle `"u_bahn"` better than `400` for natural-language → tool-arg mapping. The DB stays in GTFS native format so the ingestion pipeline doesn't need re-mapping.

---

## 8. MSS (Sozialmonitoring) — English re-labels [REMOVED in geo-context v2]

> **This section is historical.** MSS / Sozialmonitoring was removed entirely
> in geo-context v2 (ethical grounds). The source table, columns, filter, and
> the re-label map below no longer exist in the codebase. Retained only as the
> audit record of the decision that was reverted.

The source data (`social_monitoring_2025`) published German labels that were loaded with cultural meaning. We re-labelled to neutral English wherever the agent / UI surfaced MSS data.

### Status (4-level — composite of unemployment, child poverty, single-parent households on transfer benefits)

| German | English label | Semantic source |
|---|---|---|
| Status hoch | **affluent** | low unemployment, low child poverty |
| Status mittel | **mixed** | citywide average |
| Status niedrig | **lower-income** | elevated transfer-benefit dependence |
| Status sehr niedrig | **disadvantaged** | concentrated unemployment + family poverty |

### Dynamics (3-level — relative to citywide trend)

| German | English label | Semantic source |
|---|---|---|
| Dynamik positiv | **improving** | improving faster than citywide trend |
| Dynamik stabil | **stable** | tracks citywide trend |
| Dynamik negativ | **slipping** | improving slower than citywide trend (counterintuitive — even slightly improving Kieze can be labelled "negativ" if the city moved faster) |

Sources:
- https://fbinter.stadt-berlin.de/fb_daten/beschreibung/MSS/MSS_2023__TechnBeschreibung.pdf — MSS 2023 technical methodology

**Agent neutrality requirement**: `Status: affluent` is NOT a recommendation; `Status: disadvantaged` is NOT a warning. The agent must never volunteer value judgements about these labels. The "Status niedrig + Dynamik positiv" combination is the classic gentrification signature (Wedding & Neukölln in the 2010s) — a renter looking for "up-and-coming" wants exactly this; an established-character renter wants `affluent + stable`. The data is a neighborhood-character lens, not a desirability score.

This neutrality requirement is embedded in the agent's `INSTRUCTIONS` (`chat/agent.py`) so the LLM frames responses appropriately.

---

## How to add a new constant

1. Decide the constant.
2. Add a row to the relevant section of this doc with chosen value, original research, source URL, delta rationale.
3. *Then* add the constant to the relevant module under `search/`.
4. Reference this doc in the module's module-level docstring.

Constants without an entry here are technical debt — fix forward by adding the row.
