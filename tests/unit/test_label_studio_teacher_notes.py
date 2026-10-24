import unittest
import xml.etree.ElementTree as ET
from uuid import uuid4

from apartment_finder.domain.labeling import LabelingTask, TeacherStatus
from apartment_finder.infrastructure.label_studio.mapper import task_payload
from training.label_studio import build_tasks, labeling_config


class TeacherNotesTest(unittest.TestCase):
    def test_notes_replace_tips_in_sidebar_outside_region_selection(self):
        root = ET.fromstring(labeling_config())
        sidebar = root.find("./View/View[@style='flex: 1; position: sticky; top: 0']")
        self.assertIsNotNone(sidebar)
        self.assertIsNotNone(sidebar.find("./Text[@value='$review_notes']"))
        self.assertEqual(len(root.findall(".//Text[@name='review_notes']")), 1)
        for header in root.iter("Header"):
            self.assertNotIn("No name/number", header.get("value", ""))
            self.assertNotIn("street:", header.get("value", ""))
            self.assertNotIn("market:", header.get("value", ""))

    def test_offline_export_preserves_per_poi_problems(self):
        raw = {
            "id": "1", "content_hash": "a" * 64, "description": "до Южен парк",
            "title": "Имот", "city": "София", "area": "", "url": "",
        }
        problem = "pois[0]: distance value must be positive"
        teacher = {
            "content_hash": raw["content_hash"], "model": "test",
            "status": "invalid", "pois": [], "problems": [problem],
        }
        [task] = build_tasks([raw], [teacher], gold_percent=0)
        self.assertEqual(task["data"]["review_notes"], problem)
        teacher["problems"] = []
        self.assertEqual(build_tasks([raw], [teacher], 0)[0]["data"]["review_notes"], "")

    def test_api_export_displays_teacher_problem_or_empty(self):
        for problems in ([], ["pois[0]: needs a name or number"]):
            task = LabelingTask(
                id=uuid4(), listing_id=uuid4(), content_hash="a" * 64,
                description_snapshot="до Южен парк",
                teacher_status=TeacherStatus.INVALID if problems else TeacherStatus.OK,
                teacher_result=[], teacher_problems=problems,
            )
            self.assertEqual(task_payload(task).data["review_notes"], "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
