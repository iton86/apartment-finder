"""JSONL implementation of the prediction persistence port."""
from pathlib import Path

from apartment_finder.infrastructure.evaluation.jsonl import read_jsonl, write_jsonl


class JsonlPredictionRepository:
    def save(self, records: list[dict], destination: str) -> None:
        write_jsonl(Path(destination), records)

    def load(self, location: str) -> list[dict]:
        return read_jsonl(Path(location))
