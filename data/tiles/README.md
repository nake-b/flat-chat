# Protomaps tiles for the map pane

This directory is bind-mounted into the nginx container at `/usr/share/nginx/tiles/`
and served at `/tiles/`. MapLibre reads tiles via HTTP Range requests against
the `.pmtiles` file using the `pmtiles://` URL scheme.

## One-time setup (Berlin extract, ~20–40MB)

1. Install the Protomaps CLI: `brew install protomaps` (macOS) or download
   from <https://docs.protomaps.com/pmtiles/cli>.
2. Extract a Berlin bbox from the public planet pmtiles:

   ```bash
   pmtiles extract https://build.protomaps.com/$(date +%Y%m%d).pmtiles \
     ./berlin.pmtiles \
     --bbox=13.088346,52.338261,13.761160,52.675454 \
     --maxzoom=14
   ```

   The bbox is the Berlin Senate's official boundary; maxzoom 14 keeps the
   file small (no street-level details past city scale).

3. Swap the map style URL in `services/frontend/src/components/MapPane.tsx`
   from the CartoCDN demo style to a Protomaps style pointing at
   `pmtiles:///tiles/berlin.pmtiles` (or use the `protomaps-themes-base`
   package to generate a style object).

Until step 3 is done, the map renders fine using the demo style — this is
intentional so the first dev pass isn't blocked on a tile download.
