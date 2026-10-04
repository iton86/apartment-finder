"""
The POI extraction contract — the one schema shared by every stage.

The teacher model is constrained to it, Label Studio round-trips through
it, the fine-tuned model is trained to emit it and evaluated against it,
and the app adapter will parse with it. Change it here and every stage
changes together (then relabel: old labels won't match).

The model reports what the ad SAYS, never what it infers: "10 мин от МОЛ"
becomes {value: 10, unit: min_unspecified}, not an estimate in meters.
Conversion to meters happens in code (estimated_meters), where the
assumptions are visible and can be changed without retraining.

Only places the ad identifies explicitly — by a proper name or a number —
are POIs: "автобус 204" and "33 СУ" are, a bare "спирка" or "училище" is
not. Hence name is required, and find_problems checks it really is an
identifier taken from the ad.
"""

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class PoiCategory(StrEnum):
    METRO_STATION = "metro_station"
    BUS_STOP = "bus_stop"
    TRAM_STOP = "tram_stop"
    SCHOOL = "school"
    KINDERGARTEN = "kindergarten"
    UNIVERSITY = "university"
    PARK = "park"
    SHOPPING_MALL = "shopping_mall"
    SUPERMARKET = "supermarket"
    HOSPITAL = "hospital"
    MEDICAL_CENTER = "medical_center"
    PHARMACY = "pharmacy"
    GYM = "gym"
    SPORTS_FACILITY = "sports_facility"
    RESTAURANT = "restaurant"
    BANK = "bank"
    BOULEVARD = "boulevard"
    RING_ROAD = "ring_road"
    CITY_CENTER = "city_center"
    AIRPORT = "airport"
    OTHER = "other"


class DistanceUnit(StrEnum):
    METERS = "m"
    KILOMETERS = "km"
    MIN_WALK = "min_walk"
    MIN_DRIVE = "min_drive"
    MIN_TRANSIT = "min_transit"
    # "на 10 минути от ..." with no mode — the most common phrasing.
    MIN_UNSPECIFIED = "min_unspecified"
    # "в близост до", "до", "в непосредствена близост" — no number given.
    NEARBY = "nearby"


# extra="forbid" renders as additionalProperties: false, which structured
# outputs requires on every object.
class Distance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float | None = Field(
        description="The number exactly as stated in the ad; null when unit is 'nearby'."
    )
    unit: DistanceUnit


class PointOfInterest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: PoiCategory
    name: str = Field(
        description="What identifies the place, copied from the evidence: a proper name "
        "('Южен парк', 'Лидл') or a number with its word ('33 СУ', 'автобус 204')."
    )
    distance: Distance
    evidence: str = Field(
        description="Verbatim, contiguous quote from the ad that mentions this place."
    )


class PoiExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pois: list[PointOfInterest]


# --- Checks that the schema alone can't express ---------------------------

_TOKEN = re.compile(r"\S+")


def find_evidence(evidence: str, text: str) -> tuple[int, int] | None:
    """Locate evidence in text, tolerant only of whitespace and case.

    Returns character offsets into the ORIGINAL text, which is what Label
    Studio needs to highlight the span. Anything looser (fuzzy matching)
    would let paraphrased — i.e. invented — evidence through.
    """
    tokens = _TOKEN.findall(evidence)
    if not tokens:
        return None
    pattern = r"\s+".join(re.escape(t) for t in tokens)
    match = re.search(pattern, text, re.IGNORECASE)
    return (match.start(), match.end()) if match else None


def is_identifier(name: str) -> bool:
    """A number or a capitalized proper noun — what "училище" or "спирка" lack.

    Relies on names being written in normal capitalization (all-caps ad
    text gets normalized), so a generic word slipped in as a name is
    lowercase and fails. A heuristic: flagged rows go to human review.
    """
    return any(c.isdigit() or c.isupper() for c in name)


def find_problems(extraction: PoiExtraction, text: str) -> list[str]:
    """Return human-readable reasons this extraction can't be trusted as-is."""
    problems = []
    for i, poi in enumerate(extraction.pois):
        if find_evidence(poi.evidence, text) is None:
            problems.append(f"pois[{i}]: evidence not found in text: {poi.evidence!r}")
        if not poi.name.strip():
            problems.append(f"pois[{i}]: needs a name or number that identifies the place")
        elif not is_identifier(poi.name):
            problems.append(f"pois[{i}]: name {poi.name!r} is generic, not a name or number")
        elif find_evidence(poi.name, poi.evidence) is None:
            # Case-insensitive, so normalizing "ЮЖЕН ПАРК" -> "Южен парк" passes.
            problems.append(f"pois[{i}]: name {poi.name!r} does not appear in its evidence")
        if poi.distance.unit is DistanceUnit.NEARBY and poi.distance.value is not None:
            problems.append(f"pois[{i}]: unit 'nearby' must have value null")
        if poi.distance.unit is not DistanceUnit.NEARBY and poi.distance.value is None:
            problems.append(f"pois[{i}]: unit {poi.distance.unit.value!r} needs a value")
        if poi.distance.value is not None and poi.distance.value <= 0:
            problems.append(f"pois[{i}]: distance value must be positive")
    return problems


# Rough Sofia speeds, in meters per minute. Deliberately crude and kept out
# of the model's job: when better numbers exist (or real geocoding), only
# this table changes, not the training data.
_METERS_PER_MINUTE = {
    DistanceUnit.MIN_WALK: 80,
    DistanceUnit.MIN_UNSPECIFIED: 80,  # ads mostly mean walking
    DistanceUnit.MIN_TRANSIT: 250,
    DistanceUnit.MIN_DRIVE: 400,
}


def estimated_meters(distance: Distance) -> float | None:
    if distance.value is None:
        return None
    if distance.unit is DistanceUnit.METERS:
        return distance.value
    if distance.unit is DistanceUnit.KILOMETERS:
        return distance.value * 1000
    return distance.value * _METERS_PER_MINUTE[distance.unit]
