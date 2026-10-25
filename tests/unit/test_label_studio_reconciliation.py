import unittest
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from apartment_finder.domain.labeling import LabelStudioTaskPayload
from apartment_finder.infrastructure.label_studio.client import LabelStudioClient, LabelStudioError


class ListingClient(LabelStudioClient):
    def __init__(self, pages):
        super().__init__(base_url="http://label-studio.test", api_token="test")
        self.pages = pages
        self.posts = []

    def _request(self, method, path, payload=None):
        if method == "POST":
            self.posts.append(payload)
            raise AssertionError("Existing tasks must not be imported again")
        query = parse_qs(urlparse(path).query)
        assert query["fields"] == ["all"]
        return self.pages[int(query["page"][0]) - 1]


class ReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.key = uuid4()
        self.data = {"labeling_task_id": str(self.key), "content_hash": "hash", "text": "Text"}
        self.payload = LabelStudioTaskPayload(task_id=self.key, data=self.data, predictions=[])

    def task(self, task_id, annotated=False):
        return {"id": task_id, "data": self.data,
                "annotations": [{"id": 99}] if annotated else []}

    def test_total_without_next_reads_all_pages_and_does_not_import(self):
        client = ListingClient([
            {"tasks": [{"id": 1, "data": {}}], "total": 2},
            {"tasks": [self.task(2)], "total": 2},
        ])
        self.assertEqual(client.import_tasks(7, [self.payload]), {str(self.key): 2})
        self.assertEqual(client.posts, [])

    def test_count_and_next_response(self):
        client = ListingClient([
            {"results": [{"id": 1, "data": {}}], "count": 2, "next": "page2"},
            {"results": [self.task(2)], "count": 2, "next": None},
        ])
        self.assertEqual(client.import_tasks(7, [self.payload]), {str(self.key): 2})

    def test_annotated_duplicate_is_preferred_in_either_order(self):
        for tasks in ([self.task(1), self.task(2, True)],
                      [self.task(2, True), self.task(1)]):
            client = ListingClient([{"tasks": tasks, "total": 2}])
            self.assertEqual(client.import_tasks(7, [self.payload]), {str(self.key): 2})

    def test_ambiguous_annotated_duplicates_are_rejected(self):
        client = ListingClient([{"tasks": [self.task(1, True), self.task(2, True)], "total": 2}])
        with self.assertRaisesRegex(LabelStudioError, "Multiple annotated"):
            client.import_tasks(7, [self.payload])
        self.assertEqual(client.posts, [])

    def test_incomplete_unknown_or_repeated_pages_never_import(self):
        pages = [
            [{"tasks": []}],
            [{"tasks": [], "total": 1}],
            [{"tasks": [self.task(1)], "total": 2}] * 2,
            [{"unexpected": []}],
            [{"tasks": [{"id": 1, "data": None}], "total": 1}],
            [{"tasks": [{"id": 1, "data": []}], "total": 1}],
            [{"tasks": [{"id": 1, "data": ""}], "total": 1}],
            [{"tasks": [{"id": 1, "data": False}], "total": 1}],
            [{"tasks": [{"id": 1, "data": self.data}], "total": 1}],
        ]
        for response in pages:
            client = ListingClient(response)
            with self.subTest(response=response), self.assertRaises(LabelStudioError):
                client.import_tasks(7, [self.payload])
            self.assertEqual(client.posts, [])


if __name__ == "__main__":
    unittest.main()
