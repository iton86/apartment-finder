# Geospatial catalogue: implemented first stage

The initial implementation imports SofiaPlan's 564 planning units, all seven
reviewed thematic POI point layers, and GIS-Sofia's official street/surface
polygon export. It retains source bytes locally, supports offline replay,
publishes immutable PostGIS releases, and locates coordinates in planning units.

Description-based candidate resolution, official address/alias import, persistent
review and guarded calibration are now available through the
[location resolver](location-resolver-operations.md). Temporal schedules and cloud
deployment remain future work. Existing scrape/model-enrichment workflows remain
independent of location resolution.

## Isolated local database

The default Compose database now uses `postgis/postgis:16-3.5` (PostgreSQL 16).
Existing running `postgres:16` containers must be recreated with the updated
image before applying migration 0010; back up your database first. The existing
PostgreSQL major version and volume format remain 16. Use the
standalone Compose configuration for a separate database and volume on port
55432. Do not attach the existing database volume to this service.

```bash
docker compose -p apartment-finder-geo -f docker-compose.geo.yml up -d
APP_ENV=local POSTGRES_PORT=55432 uv run alembic upgrade head
```

Migration 0010 provisions PostGIS using the migration role, creates the `geo`
schema, and leaves PostGIS installed on downgrade because it may serve other
schemas. On other installations, provision the PostGIS server package first.
The default `make migrate` now also requires a PostGIS-enabled database; point it
at this instance with `POSTGRES_PORT=55432 make migrate` when developing geo.
No command here automatically migrates or replaces an existing database.

## Download and inspect

```bash
uv run apartment-finder-geo sync > data/geo-report.json
```

The default is a database-free dry run: validate JSON shapes, coordinates,
source IDs and complete ArcGIS batches; retain raw responses in
`data/geo/snapshots`; write a manifest there; print counts and its path. PostGIS
performs additional topological validation only during publication. Downloading
all source pages and the street archive takes time; raw bytes are bounded and
requests are sequential. Dry-run downloads contact the public providers.

The boundary count defaults to 564. An intentional provider change requires
review and an explicit `--expected-boundaries` override. POI codes map only when
both code and source label match a reviewed rule. Unknown or unnamed POIs remain
stored with null category, ineligible for category-based matching. Street
surface types remain original attributes; sidewalks and greenery are not
silently interpreted as carriageways.

## Publish and replay

```bash
APP_ENV=local POSTGRES_PORT=55432 uv run apartment-finder-geo sync --publish
APP_ENV=local POSTGRES_PORT=55432 uv run apartment-finder-geo sync \
  --replay-manifest data/geo/snapshots/manifest-<hash>.json --publish
```

Replay verifies local raw-file hashes and performs no provider requests. Keep
snapshots on a persistent volume and back them up with their manifest. Manifest
URIs are absolute local paths; moving snapshots requires updating their URIs
while preserving hashes. Release identity includes normalized source content and
mapping version; repeating identical content reuses a release. Replaying an old
manifest also provides explicit rollback to that retained source content.

Publication uses one PostgreSQL transaction and an advisory transaction lock.
Every feature must satisfy PostGIS type, SRID, nonempty, extent and validity
constraints. Failure rolls back the entire new release, retaining the previous
active catalogue. Other readers observe either the previous or the complete new
release. Source rows are versioned within a release: provider IDs are not assumed
stable across releases. The database stores raw-snapshot provenance in JSONB.

This first version uses one versioned feature table with explicit kind/geometry
constraints rather than separate boundary, POI and surface tables. It retains
historical releases and does not automatically prune them. Publication is manual;
concurrent manual imports serialize publication but download independently.
Provider insert/delete drift is checked by re-enumeration; simultaneous edits to
attributes and cross-provider consistency cannot be guaranteed by these APIs.

## Invalid provider polygons

The live first import found invalid topology in the source planning-unit data
(including unit 387). Strict publication rejects these polygons and rolls back.
To opt into reproducible repairs after reviewing the source, add
`--repair-invalid` to a publishing command:

```bash
APP_ENV=local POSTGRES_PORT=55432 uv run apartment-finder-geo sync \
  --replay-manifest data/geo/snapshots/manifest-<hash>.json --publish --repair-invalid
```

PostGIS applies `ST_MakeValid` to invalid polygons and retains polygon components
as MultiPolygon. Each repaired row preserves `original_geom` and
`repair_reason`; raw source snapshots also remain unchanged. Empty, out-of-bounds
or otherwise invalid repair results still abort publication. Repairs can change
boundary interpretation; shared-edge cases still return multiple units. Strict
and repaired releases have distinct identities and record their geometry policy.
Inspect repairs with `SELECT dataset, external_id, repair_reason FROM geo.feature
WHERE original_geom IS NOT NULL`. This option requires publication because
PostGIS performs the topology repair.

## Planning-unit lookup

```bash
APP_ENV=local POSTGRES_PORT=55432 uv run apartment-finder-geo locate \
  --longitude 23.32 --latitude 42.69
```

The result contains planning-unit source ID, name, and catalogue release ID.
`ST_Covers` includes boundary edges and returns all matching units. An empty list
means no covering planning unit; multiple results require ambiguity handling.
This lookup accepts independently established coordinates, not an advertisement
or an inferred position. It does not write assignments to listing rows.

## Validation

```bash
uv run ruff check .
uv run pytest tests/unit/test_geo_catalogue.py
GEO_TEST_DATABASE_URL=postgresql+psycopg://apartment_finder:local_dev_only@127.0.0.1:55432/apartment_finder \
  uv run pytest tests/integration/test_geo_catalogue.py
```

Integration tests use randomly named schemas in an explicitly configured local
PostGIS database and remove only their own schemas. Without this variable they
skip. Ordinary unit tests require neither a database nor network access.

## First live verification

On 2026-11-08, a full download and offline replay into the isolated PostGIS
instance published 564 planning units, 60,494 POIs and 91,244 surface pieces.
Opt-in repair preserved originals for 2 planning units and 14 surfaces.
The point longitude 23.32, latitude 42.69 resolved to planning unit 65,
`ЦГЧ Зона Г8`. These observations establish this source release, not a permanent
count expectation for POIs or surfaces. The existing default database container
was not modified by this verification.
