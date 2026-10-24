import unittest
from unittest.mock import Mock
from uuid import uuid4

from apartment_finder.application.ports.label_studio import LabelStudioGateway
from apartment_finder.application.ports.labeling_repository import LabelingRepository
from apartment_finder.application.use_cases.sync_annotations import SyncAnnotations
from apartment_finder.domain.labeling import LabelingTask, LabelStudioTaskState
from apartment_finder.infrastructure.label_studio.mapper import prediction_results


class SyncPortsTest(unittest.TestCase):
    def setUp(self):
        self.task = LabelingTask(
            id=uuid4(), listing_id=uuid4(), content_hash="a" * 64,
            description_snapshot="Двустаен до Южен парк.", label_studio_task_id=42,
        )
        self.repository = Mock(spec=LabelingRepository)
        self.repository.get_batch_tasks.return_value = [self.task]
        self.repository.get_by_label_studio_task_id.return_value = self.task
        self.gateway = Mock(spec=LabelStudioGateway)
        self.sync = SyncAnnotations(self.repository, self.gateway)
        self.batch_id = uuid4()

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

    def run_sync(self):
        return self.sync.execute(batch_id=self.batch_id)

    def test_review_is_persisted_through_existing_repository_port(self):
        poi = {
            "category": "park", "name": "Южен парк",
            "distance": {"value": None, "unit": "nearby"},
            "evidence": "до Южен парк",
        }
        results, _ = prediction_results([poi], self.task.description_snapshot)
        self.gateway.get_task.return_value = self.state([
            {"id": 5, "result": results, "updated_at": "2026-10-24T10:00:00Z"}
        ])
        self.assertEqual(self.run_sync().synced, 1)
        self.repository.get_batch_tasks.assert_called_once_with(self.batch_id)
        self.gateway.get_task.assert_called_once_with(42)
        saved = self.repository.sync_annotation.call_args
        self.assertEqual(saved.args, (self.task.id,))
        self.assertEqual(saved.kwargs["result"], [poi])
        self.assertEqual(saved.kwargs["annotation_id"], 5)
        self.repository.approve_batch.assert_not_called()
        self.gateway.create_annotation.assert_not_called()

    def test_saved_empty_review_is_valid(self):
        self.gateway.get_task.return_value = self.state([{"id": 5, "result": []}])
        result = self.sync.execute_label_studio_task(label_studio_task_id=42)
        self.assertEqual(result.synced, 1)
        self.assertEqual(self.repository.sync_annotation.call_args.kwargs["result"], [])

    def test_unreviewed_prediction_is_skipped(self):
        self.gateway.get_task.return_value = self.state([])
        result = self.run_sync()
        self.assertEqual((result.synced, result.skipped, result.failed), (0, 1, 0))
        self.repository.sync_annotation.assert_not_called()

    def test_deleted_previous_review_clears_saved_state(self):
        self.task = LabelingTask(**{
            **self.task.__dict__, "human_annotation_id": 5, "human_revision": 1,
        })
        self.repository.get_batch_tasks.return_value = [self.task]
        self.gateway.get_task.return_value = self.state([])
        self.assertEqual(self.run_sync().synced, 1)
        self.assertTrue(self.repository.sync_annotation.call_args.kwargs["deleted"])

    def test_malformed_annotation_is_not_saved_as_empty_review(self):
        for annotation in ({"id": 5}, {"id": 5, "result": None}, {"id": 5, "result": {}}):
            with self.subTest(annotation=annotation):
                self.gateway.get_task.return_value = self.state([annotation])
                self.assertEqual(self.run_sync().failed, 1)
        self.repository.sync_annotation.assert_not_called()
        self.assertEqual(self.repository.record_sync_error.call_count, 3)

    def test_task_failure_does_not_stop_rest_of_batch(self):
        second = LabelingTask(**{
            **self.task.__dict__, "id": uuid4(), "label_studio_task_id": 43,
        })
        self.repository.get_batch_tasks.return_value = [self.task, second]
        state = self.state([{"id": 6, "result": []}])
        state.data["labeling_task_id"] = str(second.id)
        self.gateway.get_task.side_effect = [RuntimeError("unavailable"), state]
        result = self.run_sync()
        self.assertEqual((result.synced, result.failed), (1, 1))
        self.repository.sync_annotation.assert_called_once()

    def test_single_task_not_found_reports_failure(self):
        self.repository.get_by_label_studio_task_id.return_value = None
        result = self.sync.execute_label_studio_task(label_studio_task_id=99)
        self.assertEqual(result.failed, 1)
        self.gateway.get_task.assert_not_called()


if __name__ == "__main__":
    unittest.main()
