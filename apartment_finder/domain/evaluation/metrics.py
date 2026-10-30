"""Micro-averaged exact entity metrics."""
from collections import Counter

from apartment_finder.domain.evaluation.entities import EntitySpan
from apartment_finder.domain.evaluation.matching import match


def compare(gold: list[dict], predicted: list[dict]) -> dict:
    return match([EntitySpan.from_dict(x) for x in gold],
                 [EntitySpan.from_dict(x) for x in predicted])


def summarize(records: list[dict]) -> dict:
    counts = Counter()
    for row in records:
        score = compare(row["ground_truth"], row["predictions"])
        counts.update({key: score[key] for key in ("tp", "fp", "fn")})
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": precision, "recall": recall,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
            "tp": tp, "fp": fp, "fn": fn}
