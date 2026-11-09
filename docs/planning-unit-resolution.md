# Resolve apartment advertisements to SofiaPlan planning units

## Output and confidence contract

The target is one of SofiaPlan's 564 planning units, not an administrative
district or a listing-site neighbourhood. Resolve against a pinned catalogue
release. Return ranked candidates, supporting and contradictory evidence,
resolver version, description SHA-256, and one of accepted/ambiguous/unmatched.
Location accuracy and confidence in the selected unit are separate outputs.

The map should publish only fresh accepted assignments. A unit-only assignment
is represented by its complete unit boundary and a representative marker inside
that unit, never an invented apartment coordinate. Ads assigned to the same unit
share a count marker. A score of 0.95 does not establish a 95% probability until
calibrated on reviewed examples.

## Recommended algorithm

1. **Capture observations without inference.** Preserve extracted spans and model
   confidence. Recognize street/address, neighbourhood, named place, chain/brand
   and transit-route mentions separately. Associate each distance with its place
   and travel mode. Keep negation, comparisons and copied agency boilerplate out
   of positive location evidence. The current encoder extracts names/categories,
   not distance or negation; those require another extraction stage.
2. **Resolve each mention to candidates.** Start with exact normalized names,
   reviewed aliases, school numbers and old/new street names. Expand to fuzzy
   matching only with category and settlement constraints. Retain multiple Lidl
   branches, similarly named streets and multi-campus institutions. Route 204 is
   a route, not a stop. Avoid guessing the intended feature from its nearest unit.
3. **Build spatial support regions.** A verified coordinate has measurement
   uncertainty; an address uses its verified address point; a named street uses
   all matching surface/line segments; a POI uses a distance likelihood around
   its location. Use entrances/footprints where available. Straight-line distance
   is a lower bound on travel distance, not a walking-time conversion. Learn
   broad 'nearby' kernels from labelled data rather than fixing all mentions to a
   hard radius. Account for the fact that a listing can be across a unit boundary
   from its stated POI.
4. **Generate unit candidates.** Intersect those support regions with the pinned
   planning-unit polygons. Include adjacent units in uncertain street/address or
   POI cases. A neighbourhood crosswalk is a soft many-to-many prior. A precise
   point strictly inside one unit can be decisive; a point uncertainty envelope
   touching several units cannot be treated as certain.
5. **Rank candidates.** For each unit use features such as address match quality,
   fraction of spatial likelihood inside the unit, street overlap, independently
   resolved POI support, crosswalk compatibility, contradictions and ambiguity.
   Group repeated mentions and correlated landmarks, so repeating 'Lidl' or
   describing one campus three ways does not count as three independent votes.
   A small supervised logistic/gradient-boosted ranker is a sensible first model;
   an LLM may extract evidence but should not invent final unit probabilities.
6. **Calibrate and abstain.** Train on reviewed listing-to-unit examples; split by
   physical property/duplicate ad, geography and time to avoid leakage. Calibrate
   on a held-out set (isotonic or logistic calibration), include an unknown/outside
   class, and measure accepted-set precision and coverage. Do not softmax only a
   tiny generated candidate list and interpret it as complete certainty. Start
   with an acceptance gate of calibrated P(top unit) >= 0.90 and margin over the
   second unit >= 0.15, then tune to the desired precision on held-out ads. These
   are initial policy values, not validated performance claims. Strong conflicting
   evidence forces abstention even above the threshold. A human-reviewed unit
   assignment can bypass the probability gate with explicit review provenance.
7. **Persist and refresh.** Tie every result to listing UUID, exact description
   hash, prediction/model versions, resolver/calibrator versions and catalogue
   release. Use optimistic write checks to reject edited ads. Catalogue, text or
   model changes make assignments stale. Keep historical candidates and review
   decisions. Filter expired ads from the operational map.

A practical first baseline can use exact address matching and reviewed
neighbourhood aliases, returning ambiguous for everything else. Introduce
multi-POI ranking after a labelled evaluation set exists. An ambiguous result is
preferable to a convincing-looking wrong pin.

## Spatial likelihood formulation

For evidence group e with place candidates j, construct a location likelihood
L_e(x) = sum_j P(j | mention, context) * K_e(distance(x, geometry_j)).
For each unit u integrate L_e over u, normalized over the full geographic search
region, retaining probability outside the catalogue. Feed per-group unit support
to the ranker rather than multiplying all groups under a false independence
assumption. Numeric integration may use adaptive metric grids; refine cells
near boundaries and small units, and verify against analytic point/polygon cases.

An uncertainty-aware accepted unit is not an inferred apartment point. Export a
credible area only when supported; otherwise the whole selected unit is the
reported spatial limit. Pin placement is a display decision independent of the
resolver score.

## Implemented resolver and map integration

The resolver, official references, review storage, supervised ranking and guarded
calibration workflow are implemented. See [resolver operations](location-resolver-operations.md).
The recommendation above remains the target design; metric support currently uses
conservative uniform buffers and exact intersections rather than learned travel
kernels or routing. No production model is trained without real unit labels.

## Initial file handoff

The original UI can still consume accepted assignments from an explicit local JSON handoff,
reads current listing titles and URLs from PostgreSQL, verifies description
hashes and catalogue release, and displays unit-level markers. The default UI now reads current accepted resolver results and reviews directly
from PostgreSQL. No LLM is used for final probabilities.

Only calibrated assignments meeting the configured margin, or explicitly human-
reviewed assignments, are eligible. No heuristic scores are presented as
probabilities. An empty handoff yields a working boundary map with no invented
ads. See [map operations](planning-unit-map.md) for the handoff schema and command.
