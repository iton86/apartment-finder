"""Transport enumeration annotations must preserve trainable source spans."""

import json

import pytest

from training.schema import PoiExtraction, find_problems
from training.teacher.prompt import SYSTEM_PROMPT
from training.token_data import name_spans


def test_transport_prompt_example_has_separate_trainable_routes():
    example = SYSTEM_PROMPT.split("Transport enumeration example\n<ad>\n", 1)[1]
    text, output = example.split("\n</ad>\n", 1)
    result, _ = json.JSONDecoder().raw_decode(output)
    extraction = PoiExtraction.model_validate(result)
    assert find_problems(extraction, text) == []
    expected = [
        ("204", "bus_stop"),
        ("288", "bus_stop"),
        ("76", "bus_stop"),
        ("8", "bus_stop"),
        ("11", "bus_stop"),
        ("6", "tram_stop"),
        ("27", "tram_stop"),
    ]
    assert [(poi.name, poi.category.value) for poi in extraction.pois] == expected
    spans = name_spans({"description": text, "pois": result["pois"]})
    assert [(text[start:end], category) for start, end, category in spans] == expected


@pytest.mark.parametrize(
    ("category", "evidence", "name"),
    [
        ("bus_stop", "автобусни линии 204, 288 и 76", "автобусни линии 288"),
        ("bus_stop", "тролейбусни линии 11 и 8", "тролейбусни линии 8"),
        ("tram_stop", "трамвайни линии 6 и 27", "трамвайни линии 27"),
    ],
)
def test_reconstructed_transport_names_remain_invalid(category, evidence, name):
    result = PoiExtraction.model_validate(
        {
            "pois": [
                {
                    "category": category,
                    "name": name,
                    "evidence": evidence,
                    "distance": {"value": None, "unit": "nearby"},
                }
            ]
        }
    )
    assert find_problems(result, evidence) == [
        f"pois[0]: name {name!r} does not appear in its evidence"
    ]
