# Market Maps basemaps

Street-level basemap files for the Market Maps skill, published as GitHub release
downloads so that any Claude sandbox can read them. Sandboxes block most map hosts
but allow GitHub, and these files support HTTP range requests, so the skill downloads
only the few megabytes around a study area.

## What is published

Each release holds bounded [Protomaps Basemap](https://docs.protomaps.com/basemaps/downloads)
archives (PMTiles, vector tiles to zoom 15) cut from one daily Protomaps planet build,
plus `index.json` describing every file's coverage, size, checksum and URL. The skill
reads `https://github.com/lscarroll/maps/releases/latest/download/index.json`.

Coverage is set in [`regions.json`](regions.json): the 50 US states, the District of
Columbia and Greater London. Neighbouring files overlap; any file over 1.9 GB is split
automatically into overlapping halves (GitHub's release asset limit is 2 GB).

## Updating

- Add or change an area: edit `regions.json` and push to `main`.
- Rebuild from a newer planet: runs monthly, or use **Actions → Build basemaps → Run workflow**.
- Each build publishes a new release and marks it latest only after every file uploads.

## Licence and credit

Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright),
available under the Open Database Licence (ODbL). The archives are Produced Works of
the Protomaps Basemap. Credit both on every map: `© OpenStreetMap contributors · Protomaps`.
