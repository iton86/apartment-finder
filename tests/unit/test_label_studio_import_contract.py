"""Ensure API exports supply every variable in the labeling interface."""

import json
import re
import unittest
import xml.etree.ElementTree as ET
from uuid import uuid4

from apartment_finder.domain.labeling import LabelingTask, TeacherStatus
from apartment_finder.infrastructure.label_studio.mapper import task_payload
from training.label_studio import labeling_config


class ImportContractTest(unittest.TestCase):
    def test_interface_variables_exist_in_api_task_data(self):
        for status in (TeacherStatus.MISSING, TeacherStatus.OK, TeacherStatus.ERROR):
            with self.subTest(status=status):
                task = LabelingTask(
                    id=uuid4(),
                    listing_id=uuid4(),
                    content_hash="a" * 64,
                    description_snapshot="Апартамент до Южен парк.",
                    teacher_status=status,
                    teacher_result=[] if status is TeacherStatus.OK else None,
                )
                payload = task_payload(task)
                root = ET.fromstring(labeling_config())
                required = {
                    value[1:]
                    for element in root.iter()
                    for value in element.attrib.values()
                    if re.fullmatch(r"\$\w+", value)
                }
                wire = json.loads(json.dumps({
                    "data": payload.data, "predictions": payload.predictions,
                }))
                self.assertFalse(required - wire["data"].keys())
                self.assertEqual(wire["data"]["text"], task.description_snapshot)
                self.assertIn(str(task.listing_id), wire["data"]["title"])
                self.assertIn(str(task.id), wire["data"]["meta"])
                self.assertIn("unassigned", wire["data"]["meta"])
                self.assertEqual(wire["data"]["labeling_task_id"], str(task.id))

    def test_metadata_includes_assigned_batch(self):
        task = LabelingTask(
            id=uuid4(),
            listing_id=uuid4(),
            content_hash="b" * 64,
            description_snapshot="Описание.",
            selection_batch_id=uuid4(),
        )
        self.assertIn(str(task.selection_batch_id), task_payload(task).data["meta"])


if __name__ == "__main__":
    unittest.main()
