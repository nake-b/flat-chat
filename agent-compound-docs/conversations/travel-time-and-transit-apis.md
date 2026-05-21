# Travel Time & Public Transit APIs

Research into how to provide "travel time to X" and isochrone queries for Berlin apartment search.

## Paid APIs

| Service | Transit support | Free tier | Best for |
|---|---|---|---|
| **[TravelTime API](https://traveltime.com/)** | Yes, full isochrone | 10 req/min | "What's reachable in 30 min?" — purpose-built for property search |
| **[Geoapify](https://www.geoapify.com/isoline-api/)** | Yes, isochrone + routing | 3000 credits/day (~500 isochrones) | Cheap alternative to TravelTime, good transit coverage |
| **[Google Maps Distance Matrix](https://developers.google.com/maps/documentation/distance-matrix)** | Yes | $200/month free credit | Most accurate, but expensive at scale |
| **[HERE Routing](https://www.here.com/)** | Yes, isochrone + routing | 250k req/month | Good middle ground, generous free tier |
| **[Mapbox Isochrone](https://docs.mapbox.com/api/navigation/isochrone/)** | Walk/bike/drive only | 100k req/month | No transit — skip for this use case |

## Self-hosted / free

| Service | What it is | Effort |
|---|---|---|
| **[OpenTripPlanner (OTP)](https://www.opentripplanner.org/)** | Java, the gold standard for transit routing. Feed it VBB GTFS + OSM data. GraphQL API only (REST removed in 2025). | Medium — another Docker service, ~1GB RAM for Berlin |
| **[MOTIS](https://github.com/motis-project/motis)** | C++, newer & lighter than OTP. Multimodal routing + geocoding + map tiles in one binary. Sub-second queries. | Medium — single binary, REST API, easier than OTP |
| **[Transitous](https://transitous.org/api/)** | Free public MOTIS instance with global coverage at `api.transitous.org` | **Zero** — just call the API. No SLA though |
| **[GraphHopper](https://www.graphhopper.com/)** | Java, supports GTFS import. Apache licensed. | Medium |
| **[Valhalla](https://github.com/valhalla/valhalla)** | C++, Mapbox-originated. Transit support via GTFS. | Medium-high |

## Berlin-specific data

| Resource | What |
|---|---|
| **[VBB GTFS feeds](https://daten.berlin.de/datensaetze/vbb-fahrplandaten-via-gtfs)** | Official schedule data, updated twice weekly. Powers any self-hosted option |
| **[VBB GTFS-RT](https://production.gtfsrt.vbb.de)** | Real-time vehicle positions & delays |
| **[derhuerst/vbb-modules](https://github.com/derhuerst/vbb-modules)** | Community JS modules for BVG/VBB data |

## Recommendation

**Transitous** is a sleeper pick — it's a free public MOTIS instance that already has VBB data loaded. Call `api.transitous.org` for travel time calculations with zero infrastructure. Good for MVP, swap to self-hosted MOTIS or a paid API if reliability is needed later.

Otherwise: **Geoapify** (3000 free credits/day) or **HERE** (250k/month) for quick paid integration with transit isochrones.
