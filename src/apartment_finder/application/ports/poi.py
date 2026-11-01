"""Ports for description enrichment without model or database dependencies."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class PoiCandidate:
    id: UUID
    description: str


class PoiPredictor(Protocol):
    def predict(self, description: str) -> dict: ...


class PoiRepository(Protocol):
    def pending(self, model_version: str) -> Iterable[PoiCandidate]: ...

    def save(self, candidate: PoiCandidate, prediction: dict) -> bool: ...
