"""Read saved prediction artifacts without importing MLflow."""
from apartment_finder.domain.evaluation.metrics import compare, summarize


class InspectPredictions:
    def __init__(self, repository):
        self.repository = repository

    def execute(self, location: str, error_type: str | None = None) -> dict:
        rows = self.repository.load(location)
        filtered = [row for row in rows if error_type is None or
                    any(error["type"] == error_type for error in
                        compare(row["ground_truth"], row["predictions"])["errors"])]
        return {"rows": filtered, "metrics": summarize(rows)}
