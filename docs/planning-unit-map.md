# Local planning-unit map

The local map reads SofiaPlan boundaries from the active PostGIS catalogue and
accepted ad-to-unit assignments from stored resolver results/reviews or an
explicit JSON handoff. See [resolver operations](location-resolver-operations.md). It serves only on loopback,
uses read-only database transactions, and sends no database credentials or full
listing descriptions to the browser. No website is published.

## Start

After following [geospatial setup](geospatial-operations.md), run:

```bash
APP_ENV=local POSTGRES_PORT=55432 uv run apartment-finder-geo map
```

Open **http://127.0.0.1:8765**. Change the port with `--port`. Stop with Ctrl+C.
The UI shows the active planning-unit polygons, search by name/ID, selectable
units, a basemap toggle, boundary visibility, and a reset-view button. With no
assignment file, it reads accepted results from the database. If none exist, it
shows boundaries and an explicit empty ad state.

Basemap tiles require internet access; the bundled Leaflet code and boundaries
remain local. The basemap can be disabled while keeping boundaries visible.
Normal browser caching and visible OpenStreetMap attribution are preserved;
there is no tile prefetch. Leaflet 1.9.4 is bundled under its BSD licence in
`interface/map_assets/vendor`. See [Leaflet documentation](https://leafletjs.com/reference-1.9.4.html)
and the [tile usage policy](https://operations.osmfoundation.org/policies/tiles/).

## Accepted assignment handoff

```bash
APP_ENV=local POSTGRES_PORT=55432 uv run apartment-finder-geo map \
  --assignments data/geo/planning-unit-assignments.json
```

The handoff is a JSON array, bounded to 5,000 assignments and 10 MB. Each row must
reference an existing listing UUID in **the same database** as the catalogue.
The separate initial catalogue database contains no scraped listings unless you
scrape or import listings into it. The server reads title, price and original
listing URL from those rows, not from the handoff. A live default-database
listing cannot be joined across database connections.

Calibrated machine result (placeholders must be replaced):

```json
[
  {
    "listing_id": "00000000-0000-0000-0000-000000000001",
    "planning_unit_id": "65",
    "release_id": "<64-character active catalogue SHA-256>",
    "description_sha256": "<SHA-256 of exact current listing description>",
    "resolver_version": "unit-ranker-v1",
    "calibrator_version": "heldout-calibration-v1",
    "basis": "calibrated",
    "confidence": 0.95,
    "runner_up_confidence": 0.04,
    "evidence": ["Independently resolved address and nearby school support unit 65"]
  }
]
```

The server validates the input shape, a minimum 0.90 probability, a minimum 0.15
margin, and calibrator provenance. This verifies the handoff contract; it cannot
prove that a supplied probability really came from a well-calibrated model.
Only export `basis=calibrated` after evaluating calibration on reviewed ads.
Uncalibrated heuristic/model-token scores are deliberately unsupported.

For a human-reviewed row set `basis` to `human_reviewed`, provide nonblank
`reviewed_by`, and omit confidence, runner-up confidence and calibrator version.
Reviewer provenance is required in the handoff but not displayed in the UI.
Human-reviewed assignments are shown independently of the probability slider;
the UI labels them as reviewed, without inventing a probability.

Duplicate assignments for the same listing and malformed files fail visibly.
Old catalogue releases, edited descriptions, missing planning-unit IDs, missing
listings and inactive ads are excluded. The footer counts exclusions. A refresh
re-reads the file and database so the UI does not retain a known stale response
after an error. Snapshot data is fresh as of the last successful refresh; updates
are not pushed automatically. Use an atomic file replacement when updating the
handoff while the server runs.

## Marker meaning

Confidence is **confidence in the planning unit**, not a location within it.
All ads assigned to a unit share a count marker at `ST_PointOnSurface` of its
actual polygon, guaranteed to be inside the unit. This also handles concave and
multipart polygons where a centroid could fall outside. Selecting the marker or
unit highlights the full boundary and lists the eligible ads and their evidence.
No jittered apartment coordinates or artificial street locations are generated.
The label explicitly says approximate, unit-level location. Geometry changes
produce a new release and invalidate old assignments.

The slider ranges from 90% to 100% for calibrated assignments. Search matches unit
names, unit IDs and eligible ad titles. Bad ad URLs are omitted rather than
breaking the map; titles and evidence are inserted as text, never interpreted as
HTML. Source attribution remains visible.

## Resolver and tests

The recommended [resolution algorithm](planning-unit-resolution.md) describes
candidate generation, spatial evidence, ranking, calibration and abstention.
That ranker and a persistent assignment/review workflow are now implemented;
see [resolver operations](location-resolver-operations.md).

```bash
uv run pytest tests/unit/test_planning_unit_map.py
GEO_TEST_DATABASE_URL=postgresql+psycopg://apartment_finder:local_dev_only@127.0.0.1:55432/apartment_finder \
  uv run pytest tests/integration/test_geo_catalogue.py
RUN_BROWSER_TESTS=1 uv run pytest tests/browser/test_planning_unit_map.py
```

The browser test requires an installed Playwright Chromium binary. It uses
explicit test ads without changing stored listings, and covers unit markers,
confidence filtering, human review, escaped ad text, search, reset, mobile
layout and failure recovery.

## Preview real ads before accepting assignments

To inspect suggested mappings without changing any data, keep the imported
catalogue on port 55432 and read a bounded sample of real active ads from port
5432:

```bash
APP_ENV=local POSTGRES_PORT=55432 uv run apartment-finder-geo map \
  --preview-listing-port 5432 --port 8766
```

Open http://127.0.0.1:8766. This explicit preview shows the top candidate for up to
20 ads with structured streets. It labels all suggestions unreviewed, disables
the confidence slider, and claims no probability. The true planning unit may
differ. Nothing is copied, saved, accepted or human-reviewed by preview mode.
The normal map and its confidence gates remain unchanged. Use the regular
review workflow to accept a mapping after inspecting its evidence.
