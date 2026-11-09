"""Conservative mention matching and auditable planning-unit ranking."""

import hashlib
import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher

from apartment_finder.domain.geo import fingerprint

RESOLVER_VERSION = "unit-resolver-v1"
FEATURE_NAMES = (
    "support",
    "address",
    "street",
    "poi",
    "independent",
    "conflict",
    "ambiguity",
    "quality",
    "unknown",
)
DEFAULT_WEIGHTS = (3.0, 2.0, 0.8, 0.8, 0.5, -3.0, -1.0, 0.5, 0.0)


def normalize_name(value: str) -> str:
    """Normalize punctuation and common Bulgarian street prefixes, keeping identifiers."""
    value = unicodedata.normalize("NFKC", value).casefold().replace("ѝ", "и")
    value = re.sub(r"\b(?:улица|ул|булевард|бул|град|гр)\b\.?\s*", " ", value)
    return " ".join(re.findall(r"[\w]+", value))


def description_hash(value: str) -> str:
    """Fingerprint exact source text, including whitespace and capitalization."""
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class ListingLocationInput:
    """A listing snapshot used for both resolution and optimistic writes."""

    listing_id: str
    description: str
    city: str
    area: str
    street: str | None
    pois: list[dict]
    poi_model_version: str | None
    poi_description_sha256: str | None
    observed_at: str = ""
    verified_location: dict | None = None

    @property
    def text_hash(self) -> str:
        """Return the exact description fingerprint."""
        return description_hash(self.description)

    @property
    def prediction_hash(self) -> str:
        """Include output, model and source hash so prediction edits invalidate results."""
        return fingerprint([self.pois, self.poi_model_version, self.poi_description_sha256])

    @property
    def snapshot_hash(self) -> str:
        """Include structured location and prediction state, not only description text."""
        return fingerprint(asdict(self))


@dataclass(frozen=True)
class Place:
    """One catalogue identity, with optional address fields and reviewed aliases."""

    dataset: str
    external_id: str
    name: str
    category: str
    settlement: str = ""
    number: str = ""


@dataclass
class Evidence:
    """A single independent spatial observation; duplicate mentions share one group."""

    key: str
    kind: str
    text: str
    places: list[Place]
    quality: float
    radius_m: float
    weight: float
    notes: list[str] = field(default_factory=list)


class PlaceIndex:
    """Indexed exact/category matching, bounded fuzzy fallback and street alias expansion."""

    def __init__(self, places: list[Place], aliases: list[dict] = ()):
        self.exact = defaultdict(list)
        self.tokens = defaultdict(set)
        self.addresses = defaultdict(list)
        self.aliases = defaultdict(set)
        for row in aliases:
            self.aliases[normalize_name(row["old_name"])].add(normalize_name(row["new_name"]))
        for place in places:
            key = normalize_name(place.name)
            if not key:
                continue
            if place.category == "address":
                self.addresses[(key, normalize_name(place.number))].append(place)
                continue
            self.exact[(place.category, key)].append(place)
            for token in key.split():
                if len(token) >= 3:
                    self.tokens[(place.category, token)].add(key)
            if place.category in {"school", "kindergarten"}:
                number = re.match(r"^(\d+)\b", key)
                if number:
                    self.exact[(place.category, "#" + number[1])].append(place)

    def match(self, name: str, category: str) -> tuple[list[Place], float]:
        """Retain all matching branches; never select the nearest place by assumption."""
        key = normalize_name(name)
        if not key:
            return [], 0.0
        keys = {key}
        if category == "street":
            keys |= self.aliases.get(key, set())
        matched = [p for k in sorted(keys) for p in self.exact.get((category, k), [])]
        if (
            not matched
            and category in {"school", "kindergarten"}
            and re.fullmatch(r"\d+\s+(?:су|оу|соу|ну|дг|одз)(?:\s+.*)?", key)
        ):
            matched = self.exact.get((category, "#" + key.split()[0]), [])
        if matched:
            return list({(p.dataset, p.external_id): p for p in matched}.values()), 1.0
        if len(key) < 8:
            return [], 0.0
        candidates = set()
        for token in key.split():
            candidates |= self.tokens.get((category, token), set())
        ranked = sorted(
            ((SequenceMatcher(None, key, k).ratio(), k) for k in candidates), reverse=True
        )
        if not ranked or ranked[0][0] < 0.94:
            return [], 0.0
        # Keep near ties rather than allowing fuzzy matching to erase ambiguity.
        keys = [k for score, k in ranked if score >= max(0.94, ranked[0][0] - 0.02)]
        return [p for k in keys for p in self.exact[(category, k)]], ranked[0][0]

    def address(self, street: str, number: str, city: str) -> list[Place]:
        """Resolve all entrances/branches for an exact address in the stated settlement."""
        key = normalize_name(street)
        keys = {key} | self.aliases.get(key, set())
        return [
            p
            for k in keys
            for p in self.addresses.get((k, normalize_name(number)), [])
            if normalize_name(p.settlement) == normalize_name(city)
        ]


def _excluded_context(text: str, start: int, end: int) -> bool:
    context = text[max(0, start - 70) : end + 30].casefold()
    return bool(
        re.search(
            r"(?:не\s+е\s+(?:до|близо)|далеч\s+от|без\s+достъп|"
            r"нашият\s+офис|офисът\s+ни|агенцията\s+се\s+намира|за\s+разлика\s+от)",
            context,
        )
    )


def extract_evidence(
    listing: ListingLocationInput, index: PlaceIndex
) -> tuple[list[Evidence], list[str]]:
    """Use validated spans and structured streets; uncertain contexts are omitted."""
    groups, notes = {}, []

    def add(name, category, text, radius, weight, quality_limit=1.0):
        places, quality = index.match(name, category)
        if places:
            key = fingerprint(sorted({(p.dataset, p.external_id) for p in places}))
            evidence = Evidence(
                key, category, text, places, quality * quality_limit, radius, weight
            )
            if key in groups and evidence.radius_m != groups[key].radius_m:
                evidence.radius_m = max(evidence.radius_m, groups[key].radius_m)
                evidence.notes.append("Repeated place distances merged conservatively")
                groups[key].radius_m = evidence.radius_m
            if key not in groups or evidence.weight > groups[key].weight:
                groups[key] = evidence
        else:
            notes.append(f"Unresolved {category}: {name}")

    def address_or_street(name, text, property_address=False):
        match = re.match(r"^(.+?)\s*(?:№|номер|No\.?|N)?\s+(\d+[а-яa-z]?)\s*$", name, re.I)
        if match:
            places = index.address(match[1], match[2], listing.city)
            if places:
                key = fingerprint(sorted((p.dataset, p.external_id) for p in places))
                groups[key] = Evidence(
                    key,
                    "address" if property_address else "nearby_address",
                    text,
                    places,
                    1.0,
                    30.0 if property_address else 500.0,
                    4.0 if property_address else 1.0,
                )
                return
        add(name, "street", text, 100.0, 1.0)

    if listing.verified_location:
        observation = listing.verified_location
        place = Place("verified_locations", observation["id"], observation["source"], "coordinate")
        groups["verified-coordinate"] = Evidence(
            "verified-coordinate",
            "address",
            "Verified coordinate: " + observation["source"],
            [place],
            1.0,
            observation["uncertainty_m"],
            5.0,
        )
    if listing.street and listing.street.strip():
        address_or_street(
            listing.street, "Listing street: " + listing.street, property_address=True
        )
    if listing.poi_description_sha256 != listing.text_hash:
        notes.append("POI predictions missing or stale; structured location only")
        return list(groups.values()), notes
    for poi in listing.pois:
        if not isinstance(poi, dict) or not isinstance(poi.get("category"), str):
            notes.append("Omitted malformed prediction record")
            continue
        name, category = poi.get("name"), poi.get("category")
        start, end, confidence = poi.get("start"), poi.get("end"), poi.get("confidence")
        if (
            not isinstance(name, str)
            or not name.strip()
            or type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(listing.description)
            or listing.description[start:end] != name
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0.8 <= confidence <= 1
        ):
            notes.append("Omitted invalid or low-confidence prediction span")
            continue
        if _excluded_context(listing.description, start, end):
            notes.append(f"Omitted negative/comparative/agency context: {name}")
            continue
        if category in {"bus_stop", "tram_stop", "metro_station"}:
            notes.append(f"Transit mention requires dedicated route/stop references: {name}")
            continue
        if category in {"street", "boulevard", "ring_road"}:
            suffix = listing.description[end : end + 12]
            number = re.match(r"\s*(?:№|номер)\s*(\d+[а-яa-z]?)\b", suffix, re.I)
            address_or_street(name + (" № " + number[1] if number else ""), name)
            continue
        # A distance must be directly attached to this mention, never borrowed
        # from a neighbouring POI in the same sentence.
        prefix = listing.description[max(0, start - 60) : start]
        suffix = listing.description[end : end + 45]
        before = re.search(
            r"(\d{1,4}(?:[.,]\d+)?)\s*(км|метра|метри|м)\b"
            r'\s*(?:пеша\s*)?(?:от|до)?\s*[„“"]?$',
            prefix,
            re.I,
        )
        after = re.match(
            r'^[“"]?\s*(?:е\s+)?(?:на|[-–—:])\s*'
            r"(\d{1,4}(?:[.,]\d+)?)\s*(км|метра|метри|м)\b",
            suffix,
            re.I,
        )
        attached = [match for match in (before, after) if match]
        radius = 500.0
        if len(attached) == 1:
            value, unit = attached[0].groups()
            meters = float(value.replace(",", ".")) * (1000 if unit.casefold() == "км" else 1)
            if meters <= 0 or meters * 1.5 > 3000:
                notes.append(f"Omitted invalid or overly broad distance for {name}")
                continue
            radius = max(100.0, meters * 1.5)
        elif len(attached) > 1:
            notes.append(f"Ambiguous distance association for {name}; retained broad support")
        add(name, category, name, radius, 1.0, confidence)
    # An address and its street are correlated observations, not independent votes.
    addressed = {
        normalize_name(p.name) for g in groups.values() if g.kind == "address" for p in g.places
    }
    return [
        g
        for g in groups.values()
        if not (g.kind == "street" and any(normalize_name(p.name) in addressed for p in g.places))
    ], notes


def rank_units(
    evidence: list[Evidence], supports: list[dict[str, float]], notes: list[str]
) -> dict:
    """Create explicit candidate feature vectors; scores are NOT probabilities."""
    independent = []
    for group, support in zip(evidence, supports, strict=True):
        # Co-located or duplicate landmarks have nearly identical spatial support.
        # Keep one strongest representative instead of multiplying correlated votes.
        duplicate = next(
            (
                i
                for i, (_, other) in enumerate(independent)
                if set(other) == set(support)
                and sum(abs(other[k] - support[k]) for k in other) < 0.05
            ),
            None,
        )
        if duplicate is None:
            independent.append((group, support))
        elif group.weight > independent[duplicate][0].weight:
            independent[duplicate] = (group, support)
    ids = set().union(*(support for _, support in independent)) if independent else set()
    total_weight = sum(g.weight * g.quality for g, _ in independent) or 1
    strong = [
        {unit for unit, mass in support.items() if mass >= 0.1}
        for g, support in independent
        if g.quality >= 0.9 and support
    ]
    conflict = len(strong) >= 2 and not set.intersection(*strong)
    candidates = []
    for unit in sorted(ids):
        contributions = [(g, support.get(unit, 0.0)) for g, support in independent]
        support = sum(g.weight * g.quality * mass for g, mass in contributions) / total_weight
        types = {
            kind: max(
                (mass * g.quality for g, mass in contributions if g.kind == kind), default=0.0
            )
            for kind in ("address", "street")
        }
        poi = max(
            (mass * g.quality for g, mass in contributions if g.kind not in {"address", "street"}),
            default=0.0,
        )
        independent_count = sum(mass > 0.1 for _, mass in contributions)
        ambiguity = sum(1 / max(1, len(g.places)) for g, mass in contributions if mass > 0.1)
        vector = [
            support,
            types["address"],
            types["street"],
            poi,
            min(independent_count / 3, 1),
            float(conflict),
            1 - ambiguity / max(independent_count, 1),
            max((g.quality for g, mass in contributions if mass > 0.1), default=0.0),
            0.0,
        ]
        candidates.append(
            {
                "planning_unit_id": unit,
                "features": vector,
                "score": sum(w * x for w, x in zip(DEFAULT_WEIGHTS, vector, strict=True)),
                "evidence": [g.text for g, mass in contributions if mass > 0.01],
            }
        )
    candidates.sort(key=lambda c: (-c["score"], c["planning_unit_id"]))
    return {
        "candidates": candidates,
        "conflict": conflict,
        "notes": notes,
        "evidence_groups": [
            {
                "key": g.key,
                "kind": g.kind,
                "text": g.text,
                "radius_m": g.radius_m,
                "quality": g.quality,
                "notes": g.notes,
                "source_candidates": [
                    {"dataset": p.dataset, "id": p.external_id} for p in g.places
                ],
                "support": support,
            }
            for g, support in independent
        ],
    }
