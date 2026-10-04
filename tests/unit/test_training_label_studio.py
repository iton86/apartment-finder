"""
Tests for the Label Studio round trip. The property that matters: a
teacher label imported as a prediction, accepted unchanged by a reviewer,
comes back out identical — so review only ever changes what the human
actually changed.
"""

import copy

from training.label_studio import build_tasks, convert_export, is_gold, labeling_config
from training.schema import DistanceUnit, PoiCategory

AD = "Двустаен до ЮЖЕН ПАРК, 10 мин от МОЛ БЪЛГАРИЯ."
PARK = {
    "category": "park",
    "name": "Южен парк",
    "distance": {"value": None, "unit": "nearby"},
    "evidence": "до ЮЖЕН ПАРК",
}
MALL = {
    "category": "shopping_mall",
    "name": "МОЛ България",
    "distance": {"value": 10.0, "unit": "min_unspecified"},
    "evidence": "10 мин от МОЛ БЪЛГАРИЯ",
}


def raw_row(content_hash: str = "a" * 64) -> dict:
    return {
        "id": "imot.bg:1",
        "content_hash": content_hash,
        "description": AD,
        "title": "Продава 2-СТАЕН",
        "city": "София",
        "area": "Манастирски ливади",
        "url": "https://www.imot.bg/obiava-1",
    }


def teacher_row(pois: list[dict], status: str = "ok", content_hash: str = "a" * 64) -> dict:
    return {
        "content_hash": content_hash,
        "model": "gpt-5.5",
        "status": status,
        "pois": pois,
        "problems": [],
    }


def accept_prediction(task: dict) -> dict:
    """What Label Studio exports after a reviewer submits the prediction as-is."""
    exported = copy.deepcopy(task)
    exported["id"] = 1
    exported["annotations"] = [
        {
            "result": exported["predictions"][0]["result"],
            "was_cancelled": False,
            "completed_by": 1,
            "updated_at": "2026-10-03T12:00:00Z",
        }
    ]
    return exported


class TestRoundTrip:
    def test_accepted_prediction_comes_back_unchanged_in_text_order(self):
        # Teacher lists the mall first; output is sorted by position in the ad.
        [task] = build_tasks([raw_row()], [teacher_row([MALL, PARK])], gold_percent=0)
        rows, rejected = convert_export([accept_prediction(task)], [], include_teacher=False)
        assert rejected == []
        assert rows[0]["pois"] == [PARK, MALL]
        assert rows[0]["label_source"] == "human"

    def test_reviewer_edit_is_kept(self):
        [task] = build_tasks([raw_row()], [teacher_row([MALL])], gold_percent=0)
        exported = accept_prediction(task)
        for result in exported["annotations"][0]["result"]:
            if result["from_name"] == "unit":
                result["value"]["choices"] = ["min_drive"]
        rows, _ = convert_export([exported], [], include_teacher=False)
        assert rows[0]["pois"][0]["distance"] == {"value": 10.0, "unit": "min_drive"}

    def test_submitted_with_no_regions_means_no_pois(self):
        [task] = build_tasks([raw_row()], [teacher_row([])], gold_percent=0)
        rows, _ = convert_export([accept_prediction(task)], [], include_teacher=False)
        assert rows[0]["pois"] == []

    def test_unplaceable_teacher_poi_becomes_a_review_note(self):
        bad = {**PARK, "evidence": "до Борисова градина"}
        [task] = build_tasks([raw_row()], [teacher_row([bad, MALL])], gold_percent=0)
        assert "Борисова градина" in task["data"]["review_notes"]
        assert {r["id"] for r in task["predictions"][0]["result"]} == {"t1"}

    def test_failed_teacher_row_gets_no_prediction(self):
        failed = {**teacher_row([]), "status": "refused", "pois": None, "problems": ["refusal"]}
        [task] = build_tasks([raw_row()], [failed], gold_percent=0)
        assert task["predictions"] == []
        assert "refused" in task["data"]["review_notes"]

    def test_duplicate_tasks_collapse_to_newest_human_label(self):
        teacher = [teacher_row([PARK])]
        [task] = build_tasks([raw_row()], teacher, gold_percent=0)
        older, newer = accept_prediction(task), accept_prediction(task)
        newer["annotations"][0]["updated_at"] = "2026-10-04T12:00:00Z"
        newer["annotations"][0]["result"] = []
        rows, _ = convert_export([task, newer, older], teacher, include_teacher=True)
        assert len(rows) == 1
        assert rows[0]["label_source"] == "human" and rows[0]["pois"] == []


class TestRejection:
    def test_numeric_unit_without_value_is_rejected(self):
        [task] = build_tasks([raw_row()], [teacher_row([MALL])], gold_percent=0)
        exported = accept_prediction(task)
        result = exported["annotations"][0]["result"]
        exported["annotations"][0]["result"] = [r for r in result if r["from_name"] != "value"]
        rows, rejected = convert_export([exported], [], include_teacher=False)
        assert rows == [] and "needs a value" in rejected[0]

    def test_missing_unit_is_rejected(self):
        [task] = build_tasks([raw_row()], [teacher_row([PARK])], gold_percent=0)
        exported = accept_prediction(task)
        result = exported["annotations"][0]["result"]
        exported["annotations"][0]["result"] = [r for r in result if r["from_name"] != "unit"]
        rows, rejected = convert_export([exported], [], include_teacher=False)
        assert rows == [] and len(rejected) == 1

    def test_empty_name_is_rejected(self):
        # A reviewer highlighted a place but typed no name: not identifiable.
        [task] = build_tasks([raw_row()], [teacher_row([PARK])], gold_percent=0)
        exported = accept_prediction(task)
        result = exported["annotations"][0]["result"]
        exported["annotations"][0]["result"] = [r for r in result if r["from_name"] != "name"]
        rows, rejected = convert_export([exported], [], include_teacher=False)
        assert rows == [] and "needs a name or number" in rejected[0]

    def test_cancelled_annotation_is_ignored(self):
        [task] = build_tasks([raw_row()], [teacher_row([PARK])], gold_percent=0)
        exported = accept_prediction(task)
        exported["annotations"][0]["was_cancelled"] = True
        assert convert_export([exported], [], include_teacher=False) == ([], [])


class TestTeacherFill:
    def test_fills_unreviewed_train_rows_only(self):
        teacher = [teacher_row([PARK])]
        [train] = build_tasks([raw_row()], teacher, gold_percent=0)
        [gold] = build_tasks([raw_row()], teacher, gold_percent=100)
        rows, _ = convert_export([train, gold], teacher, include_teacher=True)
        assert [(r["split"], r["label_source"]) for r in rows] == [("train", "teacher")]

    def test_never_fills_from_invalid_teacher_labels(self):
        teacher = [teacher_row([PARK], status="invalid")]
        [task] = build_tasks([raw_row()], teacher, gold_percent=0)
        assert convert_export([task], teacher, include_teacher=True) == ([], [])


class TestGoldSplit:
    def test_stable_and_roughly_proportional(self):
        hashes = [f"{i:064x}" for i in range(2000)]
        gold = [h for h in hashes if is_gold(h, 15)]
        assert 0.12 < len(gold) / len(hashes) < 0.18
        assert gold == [h for h in hashes if is_gold(h, 15)]


class TestLabelingConfig:
    def test_offers_every_category_and_unit(self):
        config = labeling_config()
        for value in [*PoiCategory, *DistanceUnit]:
            assert f'value="{value.value}"' in config


class TestSharedAndDistantEvidence:
    AD = (
        "Апартаментът е между Южен парк и НДК. На 5 минути пеша се намират "
        "метростанция Джеймс Баучер, 119 СУ и супермаркет Лидл."
    )

    def poi(self, category: str, name: str, evidence: str, unit="nearby", value=None) -> dict:
        return {
            "category": category,
            "name": name,
            "distance": {"value": value, "unit": unit},
            "evidence": evidence,
        }

    def test_one_phrase_two_places_round_trips_as_two_overlapping_regions(self):
        between = "между Южен парк и НДК"
        pois = [
            self.poi("park", "Южен парк", between),
            self.poi("other", "НДК", between),
        ]
        row = {**raw_row(), "description": self.AD}
        [task] = build_tasks([row], [teacher_row(pois)], gold_percent=0)
        spans = {
            (r["value"]["start"], r["value"]["end"])
            for r in task["predictions"][0]["result"]
            if r["type"] == "labels"
        }
        assert len(spans) == 1  # same highlighted text, two regions
        rows, rejected = convert_export([accept_prediction(task)], [], include_teacher=False)
        assert rejected == [] and rows[0]["pois"] == pois

    def test_distance_far_from_the_place_uses_one_long_span(self):
        long_span = "На 5 минути пеша се намират метростанция Джеймс Баучер, 119 СУ"
        pois = [
            self.poi(
                "metro_station",
                "Джеймс Баучер",
                "На 5 минути пеша се намират метростанция Джеймс Баучер",
                "min_walk",
                5.0,
            ),
            self.poi("school", "119 СУ", long_span, "min_walk", 5.0),
        ]
        row = {**raw_row(), "description": self.AD}
        [task] = build_tasks([row], [teacher_row(pois)], gold_percent=0)
        rows, rejected = convert_export([accept_prediction(task)], [], include_teacher=False)
        assert rejected == [] and rows[0]["pois"] == pois
