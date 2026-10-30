"""Evaluate an immutable dataset using injected model and persistence adapters."""
from apartment_finder.evaluation.domain.metrics import summarize


class EvaluateModel:
    def __init__(self, predictor, repository):
        self.predictor = predictor
        self.repository = repository

    def execute(self, rows: list[dict], destination: str) -> dict:
        records = []
        for row in rows:
            records.append({
                "id": row["id"], "text": row["text"], "split": row["split"],
                "ground_truth": row["ground_truth"],
                "predictions": self.predictor.predict(row["text"]),
            })
        self.repository.save(records, destination)
        return summarize(records)
