# Planning-unit resolver

The implemented resolver generates auditable planning-unit candidates from
independently sourced coordinates, structured street/address data, stored POI
predictions and optional reviewed neighbourhood crosswalks. It stores immutable
results and review labels in PostgreSQL. The map now reads accepted/current
results directly from the database when `--assignments` is omitted.

It includes a dependency-free supervised listwise logistic ranker, held-out
temperature calibration and an evaluation gate. No production calibration model
is bundled: existing POI training labels are not listing-to-planning-unit labels.
An uncalibrated run saves candidate rankings for review but never exposes its
heuristic scores as map confidence.

## Setup and reference updates

Use a PostGIS-enabled local database containing both listings and a published
catalogue. The separate development catalogue uses port 55432; it does not
implicitly read listings stored in the default database on port 5432.

```bash
APP_ENV=local POSTGRES_PORT=55432 uv run alembic upgrade head
APP_ENV=local POSTGRES_PORT=55432 uv run apartment-finder-locate references
```

Migration 0011 adds versioned official addresses/aliases, location observations,
resolution results, append-only reviews, and the active resolver-policy pointer.
Reference updates download complete official exports, validate counts/identities,
retain raw snapshots under `data/geo/references`, and atomically activate a new
bundle. Older bundles remain available for audit. Changing the active bundle
makes old stored map assignments ineligible until re-resolved/reviewed.

On 2026-11-08, the verified official exports contained **125,245 addresses** and
**2,158 street-name history records**. The address export supplies its own
`numberMatched`, checked against the downloaded feature count.

## Run and inspect

```bash
APP_ENV=local POSTGRES_PORT=55432 uv run apartment-finder-locate resolve --limit 100
APP_ENV=local POSTGRES_PORT=55432 uv run apartment-finder-locate report --limit 100 \
  > data/geo/location-candidates.json
```

Repeated runs skip exact previously resolved inputs and continue through active
listings in UUID pages. Changed descriptions, structured location, predictions,
POI model version, observations, catalogue, references, crosswalk or ranker model
produce new fingerprints/results. A transaction locks and rechecks all listing
inputs before saving; concurrent edits cannot be overwritten. Results include
original description/location observations for review, exact supporting source
IDs, spatial support fractions, ambiguity/contradiction notes, feature vectors,
source and model versions, and accepted/ambiguous/unmatched status.

Candidate generation uses Unicode/punctuation normalization, exact category
matches, reviewed old/new street aliases, school numbers and bounded fuzzy
matching. It retains competing chain branches and school/campus matches. Invalid
spans, stale predictions, low-confidence model spans and recognized negative,
comparative or agency-office contexts are omitted. Transit mentions are omitted
until a dedicated route/stop reference adapter is available.

Support regions use EPSG:7801 metric buffers and polygon intersection areas.
Defaults are 30 m address uncertainty, 100 m street proximity and 500 m POI
proximity. Explicit nearby address mentions remain nearby evidence, not a
property's verified address. Numeric distance mentions can widen buffers; travel
minutes are not converted into precise distances. These conservative kernels
are uncalibrated assumptions, not a learned travel model. The ranker learns from
their support features; future kernels can be introduced with a resolver-version
change and new calibration. Repeated and highly correlated evidence is grouped.
Conflicting independent support forces abstention.

Optional reviewed neighbourhood crosswalk (`--crosswalk path.json`):

```json
{
  "reviewed_by": "reviewer-id",
  "units": {"reviewed neighbourhood alias": ["65", "66"]}
}
```

Keys must use the resolver's `normalize_name`; IDs must exist in the current
catalogue. This example is a schema illustration, not an assertion that those
units cover a specific neighbourhood. A crosswalk is soft evidence, not a
neighbourhood/administrative-unit equivalence.

## Independently sourced coordinates

```bash
APP_ENV=local POSTGRES_PORT=55432 uv run apartment-finder-locate coordinate <listing-uuid> \
  --longitude 23.32 --latitude 42.69 --uncertainty-m 30 \
  --source "Independently verified address record <reference>"
```

This appends a location observation with explicit provenance. Use measured or
independently resolved property coordinates, never a nearby POI's point. A
coordinate uncertainty disk crossing a boundary supports multiple units. The
next resolve run consumes the newest observation. Updating it immediately makes
older map assignments stale. Even coordinate evidence needs calibration or a
human unit review before it becomes a pin in this first acceptance workflow.

## Human review and immediate map display

Review the original text/location and source evidence in the candidate report,
then label the result ID:

```bash
APP_ENV=local POSTGRES_PORT=55432 uv run apartment-finder-locate review <resolution-id> \
  --unit 65 --reviewer <reviewer-id> --group <physical-property-id> \
  --geography-group <spatial-holdout-group> --split train
```

Use `--unknown` instead of `--unit` for an unknown/outside/unresolvable example.
All ads for one physical property must share the same group. Geographic groups
must represent a consistent spatial partition, not arbitrary labels per ad.
Reviews are append-only; the latest review for a result controls map acceptance,
including an explicit unknown label revoking a previously accepted unit. Whitespace
is normalized in group/reviewer identities.

```bash
APP_ENV=local POSTGRES_PORT=55432 uv run apartment-finder-geo map
```

The map needs no handoff file now. Only results matching active catalogue,
reference bundle, resolver/model/crosswalk policy and exact current listing
snapshot are eligible. Human-reviewed assignments are labelled as reviewed and
have no invented probability. Ad markers remain approximate **unit-only**
locations. Explicit `--assignments` continues to select the older file-handoff
mode instead of stored results.

## Train, calibrate and evaluate

Collect at least 40 training, 20 calibration and 40 test **distinct physical
property groups**, with at least five known and five unknown/outside groups in
each split. These are code-enforced minimums, not a claim that 100 labels will
be enough for production performance. Reviews select `--split train`,
`--split calibration` or `--split test`.

Property groups, exact descriptions, geographic groups and known target units
cannot cross splits. Source listing observation times must strictly follow
train → calibration → test, so choose geography/time holdouts before reviewing.
The timestamp is the listing's stored scrape time captured with the resolution,
not the review timestamp. Duplicated property groups are represented once per
split in both training and evaluation. Cross-geography/time checks will often
require collecting more listings before a valid evaluation is possible.

```bash
APP_ENV=local POSTGRES_PORT=55432 uv run apartment-finder-locate train \
  --output data/geo/unit-ranker.json
APP_ENV=local POSTGRES_PORT=55432 uv run apartment-finder-locate resolve \
  --model data/geo/unit-ranker.json --limit 100
```

If a crosswalk was used, pass the same `--crosswalk` to train and resolve.
Models are pinned to catalogue, reference bundle, resolver/feature schema and
crosswalk. Artifacts contain data fingerprints, coefficients, calibration
temperature, split counts and untouched test metrics. Model integrity and
compatibility are checked before resolving.

The candidate ranker includes an explicit unknown class. Calibration minimizes
multiclass log loss on the calibration split. The test split reports precision,
coverage, a top-label Brier score and a 95% Wilson lower bound. Automated
acceptance requires at least 35 independently accepted test properties and a
precision lower bound >=0.90, plus top probability >=0.90, runner-up margin
>=0.15 and no strong contradictory evidence. A model failing that release gate
can still produce candidate probabilities/reports, but cannot create map pins.
Do not tune repeatedly against the same test split; collect a fresh holdout when
changing policy based on its results.

Running resolve selects the active resolver policy; changing its model/crosswalk
immediately hides earlier-policy results while new inputs are processed. This
is intentionally conservative. Switching back to the identical policy reuses
its immutable results. No Temporal auto-trigger has been added in this change;
manual commands provide a repeatable update path.

## Verification

```bash
uv run pytest tests/unit/test_location_resolution.py tests/unit/test_resolution_references.py
GEO_TEST_DATABASE_URL=postgresql+psycopg://apartment_finder:local_dev_only@127.0.0.1:55432/apartment_finder \
  uv run pytest tests/integration/test_location_resolution.py
```

Tests use synthetic labels only to validate code; no synthetic model is installed
or represented as calibrated on real Sofia advertisements.
