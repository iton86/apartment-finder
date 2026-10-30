from apartment_finder.inspection.application.compare_runs import compare_runs
from apartment_finder.evaluation.application.evaluate_model import EvaluateModel
from apartment_finder.evaluation.domain.metrics import compare, summarize


class FakePredictor:
    def predict(self, text):
        return [{"start": 0, "end": 4, "category": "park"}]


class FakeRepository:
    def save(self, records, destination):
        self.records = records


def test_evaluate_model_uses_ports():
    repository = FakeRepository()
    rows = [{"id": 1, "text": "Park", "split": "validation",
             "ground_truth": [{"start": 0, "end": 4, "category": "park"}]}]
    result = EvaluateModel(FakePredictor(), repository).execute(rows, "unused")
    assert result["f1"] == 1.0
    assert repository.records[0]["id"] == 1


def test_compare_runs_rejects_changed_gold():
    base = [{"id": 1, "text": "Park", "ground_truth": [],
             "predictions": []}]
    other = [{"id": 1, "text": "Park", "ground_truth": [{"start": 0, "end": 4, "category": "park"}],
              "predictions": []}]
    assert compare_runs(base, other) == []


def test_boundary_mismatch_counts_both_errors():
    result = compare([{"start": 0, "end": 4, "category": "park"}],
                     [{"start": 0, "end": 3, "category": "park"}])
    assert (result["tp"], result["fp"], result["fn"]) == (0, 1, 1)
