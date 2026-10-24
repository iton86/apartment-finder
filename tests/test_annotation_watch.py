import argparse
import unittest
from unittest.mock import Mock
from uuid import uuid4

from apartment_finder.application.use_cases.sync_annotations import (
    SyncAnnotations,
    SyncAnnotationsResult,
)
from apartment_finder.domain.labeling import LabelingTask, LabelStudioTaskState
from apartment_finder.infrastructure.label_studio.mapper import prediction_results
from apartment_finder.interface.annotation_sync import positive_interval, watch_annotations


class AnnotationWatchTest(unittest.TestCase):
    def setUp(self):
        self.task = LabelingTask(
            id=uuid4(), listing_id=uuid4(), content_hash="a" * 64,
            description_snapshot="Двустаен до Южен парк.", label_studio_task_id=42,
        )
        self.repository = Mock()
        self.repository.get_exported_tasks.return_value = [self.task]
        self.repository.get_batch_tasks.return_value = [self.task]
        self.gateway = Mock()
        self.sync = SyncAnnotations(self.repository, self.gateway)

    def state(self, annotations):
        return LabelStudioTaskState(
            task_id=42,
            data={
                "labeling_task_id": str(self.task.id),
                "content_hash": self.task.content_hash,
                "text": self.task.description_snapshot,
            },
            annotations=annotations,
        )

    def test_all_exported_tasks_are_reloaded_each_pass(self):
        self.gateway.get_task.return_value = self.state([])
        self.sync.execute()
        self.repository.get_exported_tasks.return_value = []
        self.sync.execute()
        self.assertEqual(self.repository.get_exported_tasks.call_count, 2)
        self.gateway.get_task.assert_called_once_with(42)
        self.repository.get_batch_tasks.assert_not_called()

    def test_batch_scope_stays_supported(self):
        batch_id = uuid4()
        self.gateway.get_task.return_value = self.state([])
        self.sync.execute(batch_id=batch_id)
        self.repository.get_batch_tasks.assert_called_once_with(batch_id)
        self.repository.get_exported_tasks.assert_not_called()

    def test_saved_review_edits_and_deletion_are_pulled(self):
        poi = {
            "category": "park", "name": "Южен парк",
            "distance": {"value": None, "unit": "nearby"},
            "evidence": "до Южен парк",
        }
        result, _ = prediction_results([poi], self.task.description_snapshot)
        self.gateway.get_task.side_effect = [
            self.state([{"id": 1, "result": result, "updated_at": "2026-10-24T10:00:00Z"}]),
            self.state([{"id": 1, "result": [], "updated_at": "2026-10-24T10:01:00Z"}]),
            self.state([]),
        ]
        for _ in range(3):
            self.assertEqual(self.sync.execute().failed, 0)
        calls = self.repository.sync_annotation.call_args_list
        self.assertEqual(calls[0].kwargs["result"], [poi])
        self.assertEqual(calls[1].kwargs["result"], [])
        self.assertNotEqual(calls[0].kwargs["revision"], calls[1].kwargs["revision"])
        self.assertTrue(calls[2].kwargs["deleted"])
        self.repository.approve_batch.assert_not_called()
        self.gateway.create_annotation.assert_not_called()

    def test_predictions_are_not_imported_as_human_reviews(self):
        state = self.state([])
        state = LabelStudioTaskState(
            task_id=state.task_id, data=state.data,
            predictions=[{"result": [{"teacher": "prediction"}]}],
        )
        self.gateway.get_task.return_value = state
        self.sync.execute()
        self.assertIsNone(self.repository.sync_annotation.call_args.kwargs["result"])

    def test_watch_retries_failed_pass_and_waits_between_passes(self):
        sync = Mock()
        sync.execute.side_effect = [
            RuntimeError("database unavailable"), SyncAnnotationsResult(1, 0)
        ]
        stop = Mock()
        stop.is_set.side_effect = [False, False, True]
        with self.assertLogs("apartment_finder.interface.annotation_sync", level="INFO"):
            watch_annotations(sync, interval=10, stop_event=stop)
        self.assertEqual(sync.execute.call_count, 2)
        self.assertEqual(stop.wait.call_count, 2)
        stop.wait.assert_called_with(10)

    def test_stopped_watch_does_not_fetch(self):
        sync, stop = Mock(), Mock()
        stop.is_set.return_value = True
        watch_annotations(sync, stop_event=stop)
        sync.execute.assert_not_called()

    def test_interval_validation(self):
        self.assertEqual(positive_interval("2.5"), 2.5)
        for value in ("0", "-1", "nan", "inf", "oops"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                positive_interval(value)


if __name__ == "__main__":
    unittest.main()
