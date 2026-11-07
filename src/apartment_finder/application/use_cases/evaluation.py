"""Model evaluation use cases: score a dataset, inspect saved predictions, compare runs."""

from apartment_finder.domain.evaluation import compare, summarize


class EvaluateModel:
    """Run a model over a labeled dataset, persist the predictions and score them."""

    def __init__(self, predictor, repository):
        self.predictor = predictor
        self.repository = repository

    def execute(self, rows: list[dict], destination: str) -> dict:
        """Predict every row's text, save the records to ``destination`` and return the metrics."""
        records = []
        for row in rows:
            records.append(
                {
                    "id": row["id"],
                    "text": row["text"],
                    "split": row["split"],
                    "ground_truth": row["ground_truth"],
                    "predictions": self.predictor.predict(row["text"]),
                }
            )
        self.repository.save(records, destination)
        return summarize(records)


class InspectPredictions:
    """Load saved predictions for review, optionally narrowed to one kind of error."""

    def __init__(self, repository):
        self.repository = repository

    def execute(self, location: str, error_type: str | None = None) -> dict:
        """Return the rows at ``location`` plus metrics.

        Only rows with at least one error of ``error_type`` ("FP" or "FN") are listed when
        it is given; the metrics always cover all rows.
        """
        rows = self.repository.load(location)
        filtered = [
            row
            for row in rows
            if error_type is None
            or any(
                error["type"] == error_type
                for error in compare(row["ground_truth"], row["predictions"])["errors"]
            )
        ]
        return {"rows": filtered, "metrics": summarize(rows)}


def compare_runs(left: list[dict], right: list[dict]) -> list[dict]:
    """Compare two runs sample by sample, with ``left`` as the baseline.

    Only samples present in both runs with identical text and ground truth are compared,
    so differences reflect the model and not changed data. ``delta`` is the change in
    error count (FP + FN); negative means ``right`` is better.
    """
    baseline = {str(row["id"]): row for row in left}
    differences = []
    for row in right:
        original = baseline.get(str(row["id"]))
        if (
            original is None
            or original["text"] != row["text"]
            or original["ground_truth"] != row["ground_truth"]
        ):
            continue
        a = compare(original["ground_truth"], original["predictions"])
        b = compare(row["ground_truth"], row["predictions"])
        differences.append(
            {
                "id": row["id"],
                "baseline_errors": a["fp"] + a["fn"],
                "other_errors": b["fp"] + b["fn"],
                "delta": b["fp"] + b["fn"] - a["fp"] - a["fn"],
            }
        )
    return differences
