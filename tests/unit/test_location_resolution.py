"""Conservative mention matching, ambiguity and calibrated acceptance regression tests."""

from dataclasses import replace
from uuid import uuid4

import pytest

from apartment_finder.application.use_cases.resolve_listing_locations import (
    resolve_listing_locations,
)
from apartment_finder.domain.geo import fingerprint
from apartment_finder.domain.location_model import (
    model_ready,
    probabilities,
    train_location_model,
    validate_model,
)
from apartment_finder.domain.location_resolution import (
    Evidence,
    ListingLocationInput,
    Place,
    PlaceIndex,
    description_hash,
    extract_evidence,
    normalize_name,
    rank_units,
)


def listing(text="До Болница Алфа", **changes):
    name = "Болница Алфа"
    start = text.find(name)
    base = ListingLocationInput(
        str(uuid4()),
        text,
        "София",
        "Лозенец",
        None,
        [
            {
                "name": name,
                "category": "hospital",
                "start": start,
                "end": start + len(name),
                "confidence": 0.99,
            }
        ],
        "poi-v1",
        description_hash(text),
    )
    return replace(base, **changes)


def test_normalization_and_ambiguous_branches():
    assert normalize_name("Бул. „Черни връх“") == "черни връх"
    assert normalize_name("Градски паркове") == "градски паркове"
    places = [Place("poi_19", str(i), "Лидл", "supermarket") for i in (1, 2)]
    assert len(PlaceIndex(places).match("ЛИДЛ", "supermarket")[0]) == 2
    assert PlaceIndex(places).match("Лидл", "hospital")[0] == []


def test_address_alias_retains_entrances_and_settlement():
    places = [
        Place("official_addresses", str(i), "ул. Нова", "address", city, "12А")
        for i, city in [(1, "гр. София"), (2, "гр. София"), (3, "гр. Банкя")]
    ]
    index = PlaceIndex(places, [{"old_name": "ул. Стара", "new_name": "ул. Нова"}])
    assert len(index.address("Стара", "12а", "София")) == 2
    groups, _ = extract_evidence(listing(street="ул. Стара № 12А"), index)
    assert len(groups) == 1 and groups[0].kind == "address"


def test_school_number_and_bounded_fuzzy_matching():
    index = PlaceIndex(
        [
            Place("poi_13", "1", "33 СУ Васил Левски", "school"),
            Place("poi_4", "2", "Болница Александровска", "hospital"),
        ]
    )
    assert index.match("33 СУ", "school")[0][0].external_id == "1"
    assert index.match("Болница Александровсkа", "hospital")[1] >= 0.94
    assert index.match("болница", "hospital")[0] == []


@pytest.mark.parametrize(
    "text", ["Не е близо до Болница Алфа", "Далеч от Болница Алфа", "Нашият офис е до Болница Алфа"]
)
def test_negative_and_boilerplate_excluded(text):
    index = PlaceIndex([Place("poi_13", "1", "Болница Алфа", "hospital")])
    assert extract_evidence(listing(text), index)[0] == []


def test_stale_predictions_and_duplicate_mentions():
    index = PlaceIndex([Place("poi_13", "1", "Болница Алфа", "hospital")])
    original = listing()
    assert not extract_evidence(replace(original, poi_description_sha256="stale"), index)[0]
    double = replace(original, pois=original.pois * 2)
    assert len(extract_evidence(double, index)[0]) == 1
    route = listing(
        "автобус 204",
        pois=[{"name": "204", "category": "bus_stop", "start": 8, "end": 11, "confidence": 0.99}],
    )
    assert not extract_evidence(route, index)[0]


def test_metric_distance_is_evidence_not_invented_walking_conversion():
    index = PlaceIndex([Place("poi_13", "1", "Болница Алфа", "hospital")])
    group = extract_evidence(listing("На 200 м от Болница Алфа"), index)[0][0]
    assert group.radius_m == 300
    group = extract_evidence(listing("На 10 минути от Болница Алфа"), index)[0][0]
    assert group.radius_m == 500


def test_correlation_and_conflicting_support():
    p = Place("poi", "1", "Hospital", "hospital")
    groups = [Evidence(str(i), "hospital", "Mention", [p], 1, 500, 1) for i in range(2)]
    result = rank_units(groups, [{"1": 0.8, "2": 0.2}, {"1": 0.8, "2": 0.2}], [])
    assert len(result["evidence_groups"]) == 1
    assert not result["conflict"]
    result = rank_units(groups, [{"1": 1}, {"2": 1}], [])
    assert result["conflict"]


class FakeRepository:
    release_id = "a" * 64
    bundle_id = None

    def __init__(self):
        self.saved = []
        self.row = listing()

    def activate_policy(self, policy):
        return fingerprint(policy)

    def index(self):
        return PlaceIndex([Place("poi_13", "1", "Болница Алфа", "hospital")])

    def listings(self):
        yield self.row

    def support(self, *args):
        return {"65": 0.95, "66": 0.05}

    def exists(self, *args):
        return False

    def save(self, row, result):
        self.saved.append(result)
        return True


def test_uncalibrated_rankings_never_become_map_assignments():
    repo = FakeRepository()
    counts = resolve_listing_locations(repo)["counts"]
    assert counts["ambiguous"] == 1 and counts["accepted"] == 0
    assert repo.saved[0]["assignment"] is None
    assert repo.saved[0]["candidates"][0]["planning_unit_id"] == "65"


def examples():
    result = []
    for split, count in [("train", 40), ("calibration", 20), ("test", 40)]:
        for i in range(count):
            unknown = i < 5
            feature = (
                [0.05, 0, 0, 0.05, 0, 0, 1, 0.8, 0] if unknown else [1, 1, 0, 0, 1, 0, 0, 1, 0]
            )
            result.append(
                {
                    "listing_id": f"{split}-{i}",
                    "group_id": f"{split}-{i}",
                    "split": split,
                    "description_sha256": description_hash(f"{split}-{i}"),
                    "geography_group": split + "-geo",
                    "observed_at": {
                        "train": "2024-01-01T00:00:00+00:00",
                        "calibration": "2025-01-01T00:00:00+00:00",
                        "test": "2026-01-01T00:00:00+00:00",
                    }[split],
                    "planning_unit_id": None if unknown else split + "65",
                    "result": {
                        "candidates": [{"planning_unit_id": split + "65", "features": feature}],
                        "conflict": False,
                    },
                }
            )
    return result


def test_train_calibrate_evaluate_unknown_and_artifact_integrity():
    model = train_location_model(examples(), {"release_id": "a" * 64})
    validate_model(model, {"release_id": "a" * 64})
    metrics = model["test_metrics"]
    assert metrics["correct"] == metrics["accepted"]
    assert model_ready(model)
    ranked = probabilities(examples()[-1]["result"]["candidates"], model)
    assert sum(p for _, p in ranked) == pytest.approx(1)
    assert any(unit is None for unit, _ in ranked)
    with pytest.raises(ValueError, match="fingerprint"):
        validate_model(model | {"temperature": 2}, {"release_id": "a" * 64})
    with pytest.raises(ValueError, match="does not match"):
        validate_model(model, {"release_id": "b" * 64})
    weak = model | {"test_metrics": metrics | {"correct": 0}}
    assert not model_ready(weak)


def test_group_and_duplicate_text_leakage_rejected():
    rows = examples()
    rows[-1]["group_id"] = rows[0]["group_id"]
    with pytest.raises(ValueError, match="leakage"):
        train_location_model(rows, {})
    rows = examples()
    rows[-1]["description_sha256"] = rows[0]["description_sha256"]
    with pytest.raises(ValueError, match="leakage"):
        train_location_model(rows, {})


def test_supervised_model_can_emit_valid_map_handoff():
    from apartment_finder.application.use_cases.resolve_listing_locations import resolver_context

    repo = FakeRepository()
    context = resolver_context(repo, {})
    model = train_location_model(examples(), context)
    # Synthetic address-like QA observation; not production calibration.
    original = repo.support
    repo.support = lambda *args: {"65": 1.0}
    repo.row = listing(street="ул. Нова № 12")
    repo.index = lambda: PlaceIndex(
        [Place("official_addresses", "1", "ул. Нова", "address", "София", "12")]
    )
    resolve_listing_locations(repo, model=model)
    assert repo.saved[0]["status"] == "accepted"
    assert repo.saved[0]["assignment"]["basis"] == "calibrated"
    repo.support = original


def test_geographic_and_temporal_holdouts_are_required():
    rows = examples()
    rows[-1]["geography_group"] = rows[0]["geography_group"]
    with pytest.raises(ValueError, match="Geographic"):
        train_location_model(rows, {})
    rows = examples()
    rows[-1]["observed_at"] = rows[0]["observed_at"]
    with pytest.raises(ValueError, match="Chronological"):
        train_location_model(rows, {})


def test_whitespace_property_aliases_do_not_inflate_test_examples():
    rows = examples()
    extra = rows[-1] | {"listing_id": "another-ad", "group_id": " " + rows[-1]["group_id"] + " "}
    model = train_location_model(rows + [extra], {})
    assert model["test_metrics"]["examples"] == 40


def test_insufficient_labels_cannot_enable_acceptance():
    with pytest.raises(ValueError, match="40 train"):
        train_location_model(examples()[:5], {})


def test_nearby_address_mention_does_not_claim_property_address():
    text = "До ул. Нова № 12"
    row = listing(
        text,
        pois=[
            {"name": "ул. Нова", "category": "street", "start": 3, "end": 11, "confidence": 0.99}
        ],
    )
    index = PlaceIndex([Place("official_addresses", "1", "ул. Нова", "address", "София", "12")])
    groups, _ = extract_evidence(row, index)
    assert groups[0].kind == "nearby_address" and groups[0].radius_m == 500


def test_malformed_predictions_are_omitted_instead_of_crashing_batch():
    row = listing(pois=["broken", {"category": []}])
    groups, notes = extract_evidence(row, PlaceIndex([]))
    assert groups == [] and len(notes) == 2


def test_distance_is_not_borrowed_from_neighbouring_poi():
    text = "100 м от Лидл и близо до Кауфланд"
    pois = []
    for name in ["Лидл", "Кауфланд"]:
        start = text.index(name)
        pois.append(
            {
                "name": name,
                "category": "supermarket",
                "start": start,
                "end": start + len(name),
                "confidence": 0.99,
            }
        )
    row = listing(text, pois=pois)
    index = PlaceIndex(
        [
            Place("poi_19", "1", "Лидл", "supermarket"),
            Place("poi_19", "2", "Кауфланд", "supermarket"),
        ]
    )
    groups, _ = extract_evidence(row, index)
    assert {group.text: group.radius_m for group in groups} == {"Лидл": 150, "Кауфланд": 500}
