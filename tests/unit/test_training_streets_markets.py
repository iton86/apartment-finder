"""Offline contract checks; these do not measure a live teacher's recall."""

import json
import re
import unittest
import xml.etree.ElementTree as ET

from training.label_studio import build_tasks, convert_export, labeling_config
from training.schema import PoiExtraction, find_problems
from training.teacher.prompt import SYSTEM_PROMPT


class StreetMarketContractTest(unittest.TestCase):
    def test_prompt_examples_validate_and_round_trip(self):
        examples = [
            (match.group(1), json.JSONDecoder().raw_decode(SYSTEM_PROMPT[match.end():])[0])
            for match in re.finditer(r"<ad>\n(.*?)\n</ad>\n", SYSTEM_PROMPT, re.DOTALL)
        ]
        self.assertEqual(len(examples), 2)
        for text, output in examples:
            extraction = PoiExtraction.model_validate(output)
            self.assertEqual(find_problems(extraction, text), [])
            pois = extraction.model_dump(mode="json")["pois"]
            raw = {
                "id": "imot.bg:1", "content_hash": "a" * 64,
                "title": "Продава", "city": "София", "area": "",
                "url": "https://example.com/1", "description": text,
            }
            teacher = {
                "content_hash": raw["content_hash"], "model": "test",
                "status": "ok", "problems": [], "pois": pois,
            }
            [task] = build_tasks([raw], [teacher], gold_percent=0)
            task["annotations"] = [{
                "result": task["predictions"][0]["result"],
                "updated_at": "2026-10-24T00:00:00Z",
            }]
            rows, rejected = convert_export([task], [], include_teacher=False)
            self.assertEqual(rejected, [])
            self.assertEqual(rows[0]["pois"], pois)

    def test_street_and_market_example_keeps_names_and_distances(self):
        example = SYSTEM_PROMPT.split("Street and market example\n", 1)[1]
        output = example.split("</ad>\n", 1)[1].split("\nSkipped:", 1)[0]
        pois = json.loads(output)["pois"]
        self.assertEqual([p["category"] for p in pois], ["street", "market"])
        self.assertEqual(pois[0]["name"], 'ул. "Цар Симеон"')
        self.assertEqual(pois[0]["distance"], {"value": 200, "unit": "m"})
        self.assertEqual(pois[1]["name"], "Женски пазар")
        self.assertEqual(pois[1]["distance"], {"value": 5, "unit": "min_walk"})

    def test_reviewer_guidance_is_valid_xml(self):
        root = ET.fromstring(labeling_config())
        guidance = " ".join(h.get("value", "") for h in root.iter("Header"))
        self.assertIn("street: named ул / ул. / улица", guidance)
        self.assertIn("market: named пазар / пазара / пазарът", guidance)
        labels = {label.get("value") for label in root.iter("Label")}
        self.assertTrue({"street", "market"} <= labels)


if __name__ == "__main__":
    unittest.main()
