"""
Tests for the POI schema checks that pydantic alone can't express:
evidence must really occur in the ad, the name must be an identifier
taken from it, and distance value/unit must agree.
"""

import pytest

from training.schema import (
    Distance,
    DistanceUnit,
    PoiExtraction,
    estimated_meters,
    find_evidence,
    find_problems,
)

AD = "Двустаен до ЮЖЕН ПАРК,\n10 мин   от МОЛ БЪЛГАРИЯ и на 300 м от спирка на автобус 204."


def extraction(*pois: dict) -> PoiExtraction:
    return PoiExtraction.model_validate({"pois": list(pois)})


def poi(
    evidence: str, unit: str = "nearby", value: float | None = None, name: str = "Южен парк"
) -> dict:
    return {
        "category": "park",
        "name": name,
        "distance": {"value": value, "unit": unit},
        "evidence": evidence,
    }


class TestFindEvidence:
    def test_exact(self):
        start, end = find_evidence("до ЮЖЕН ПАРК", AD)
        assert AD[start:end] == "до ЮЖЕН ПАРК"

    def test_tolerates_whitespace_and_case_but_returns_original_span(self):
        start, end = find_evidence("10 мин от мол българия", AD)
        assert AD[start:end] == "10 мин   от МОЛ БЪЛГАРИЯ"

    @pytest.mark.parametrize("evidence", ["до Южния парк", "", "   "])
    def test_paraphrase_or_empty_is_not_found(self, evidence):
        assert find_evidence(evidence, AD) is None

    def test_regex_characters_are_literal(self):
        assert find_evidence("(бул.)", "близо до (бул.) X") == (9, 15)


class TestFindProblems:
    def test_clean_extraction(self):
        result = extraction(
            poi("до ЮЖЕН ПАРК"),
            poi("на 300 м от спирка на автобус 204", "m", 300, name="автобус 204"),
        )
        assert find_problems(result, AD) == []

    def test_invented_evidence(self):
        problems = find_problems(
            extraction(poi("до Борисова градина", name="Борисова градина")), AD
        )
        assert "evidence not found" in problems[0]

    @pytest.mark.parametrize(
        ("unit", "value", "message"),
        [
            ("nearby", 5, "must have value null"),
            ("min_walk", None, "needs a value"),
            ("m", 0, "must be positive"),
        ],
    )
    def test_inconsistent_distance(self, unit, value, message):
        problems = find_problems(extraction(poi("до ЮЖЕН ПАРК", unit, value)), AD)
        assert len(problems) == 1 and message in problems[0]

    @pytest.mark.parametrize(
        ("name", "evidence"),
        [
            ("автобус 204", "спирка на автобус 204"),
            ("Южен парк", "до ЮЖЕН ПАРК"),  # capitalization normalized
            ("МОЛ България", "10 мин   от МОЛ БЪЛГАРИЯ"),
        ],
    )
    def test_identifying_names_pass(self, name, evidence):
        assert find_problems(extraction(poi(evidence, name=name)), AD) == []

    @pytest.mark.parametrize(
        ("name", "message"),
        [
            ("", "needs a name or number"),
            ("   ", "needs a name or number"),
            ("спирка", "is generic"),
            ("метростанция", "is generic"),
        ],
    )
    def test_unidentified_places_are_flagged(self, name, message):
        problems = find_problems(extraction(poi("на 300 м от спирка", name=name)), AD)
        assert len(problems) == 1 and message in problems[0]

    def test_name_must_come_from_the_evidence(self):
        # A real name, but the teacher invented it: the ad never says it here.
        problems = find_problems(extraction(poi("до ЮЖЕН ПАРК", name="Борисова градина")), AD)
        assert len(problems) == 1 and "does not appear in its evidence" in problems[0]

    def test_empty_is_valid(self):
        assert find_problems(extraction(), AD) == []


class TestEstimatedMeters:
    @pytest.mark.parametrize(
        ("unit", "value", "meters"),
        [
            ("m", 300, 300),
            ("km", 1.5, 1500),
            ("min_walk", 5, 400),
            ("min_unspecified", 10, 800),
            ("min_drive", 10, 4000),
            ("nearby", None, None),
        ],
    )
    def test_conversion(self, unit, value, meters):
        assert estimated_meters(Distance(value=value, unit=DistanceUnit(unit))) == meters
