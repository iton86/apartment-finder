"""Exact-match POI evaluation: entity spans, BIO decoding, matching and micro-averaged metrics."""

from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class EntitySpan:
    """A labeled character span; ``end`` is exclusive.

    Raises:
        ValueError: If the offsets are negative or empty, or the category is blank.
    """

    start: int
    end: int
    category: str
    confidence: float | None = None

    def __post_init__(self):
        if self.start < 0 or self.end <= self.start or not self.category:
            raise ValueError("Invalid entity span")

    @classmethod
    def from_dict(cls, value: dict):
        """Build a span from a dict with start, end, category and optional confidence."""
        return cls(value["start"], value["end"], value["category"], value.get("confidence"))

    def identity(self):
        """Return the (start, end, category) key used for exact matching; confidence is ignored."""
        return (self.start, self.end, self.category)


def entities(tags: list[str]) -> set[tuple[int, int, str]]:
    """Decode BIO spans; treat orphan I tags as a new entity for scoring."""
    result = set()
    start, category = None, None
    for i, tag in enumerate([*tags, "O"]):
        prefix, _, current = tag.partition("-")
        if start is not None and (prefix != "I" or current != category):
            result.add((start, i, category))
            start, category = None, None
        if prefix in {"B", "I"} and start is None:
            start, category = i, current
    return result


def match(gold: list[EntitySpan], predicted: list[EntitySpan]) -> dict:
    """Score predicted spans against gold ones by exact (start, end, category) match.

    Returns:
        Counts ``tp``, ``fp`` and ``fn`` plus ``errors``, a sorted list of FN then FP spans.
    """
    expected = {span.identity() for span in gold}
    actual = {span.identity() for span in predicted}
    correct = expected & actual
    return {
        "tp": len(correct),
        "fp": len(actual - correct),
        "fn": len(expected - correct),
        "errors": [
            *({"type": "FN", "span": span} for span in sorted(expected - correct)),
            *({"type": "FP", "span": span} for span in sorted(actual - correct)),
        ],
    }


def compare(gold: list[dict], predicted: list[dict]) -> dict:
    """Like ``match``, but takes spans as plain dicts, as stored in prediction records."""
    return match(
        [EntitySpan.from_dict(x) for x in gold], [EntitySpan.from_dict(x) for x in predicted]
    )


def summarize(records: list[dict]) -> dict:
    """Micro-average precision, recall and F1 over all records (0.0 when undefined)."""
    counts = Counter()
    for row in records:
        score = compare(row["ground_truth"], row["predictions"])
        counts.update({key: score[key] for key in ("tp", "fp", "fn")})
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }
