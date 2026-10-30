"""Immutable POI evaluation value objects."""
from dataclasses import dataclass


@dataclass(frozen=True)
class EntitySpan:
    start: int
    end: int
    category: str
    confidence: float | None = None

    def __post_init__(self):
        if self.start < 0 or self.end <= self.start or not self.category:
            raise ValueError("Invalid entity span")

    @classmethod
    def from_dict(cls, value: dict):
        return cls(value["start"], value["end"], value["category"], value.get("confidence"))

    def identity(self):
        return (self.start, self.end, self.category)
