"""Exact span matching and sample-level POI error classification."""
from collections import Counter


def compare(gold, predicted):
    expected = {(e["start"], e["end"], e["category"]) for e in gold}
    actual = {(e["start"], e["end"], e["category"]) for e in predicted}
    tp = expected & actual
    missed = expected - tp
    extra = actual - tp
    errors = []
    for span in sorted(missed):
        errors.append({"type": "FN", "span": span})
    for span in sorted(extra):
        errors.append({"type": "FP", "span": span})
    return {"tp": len(tp), "fp": len(extra), "fn": len(missed), "errors": errors}


def summarize(records):
    counts = Counter()
    for row in records:
        result = compare(row["ground_truth"], row["predictions"])
        counts.update({key: result[key] for key in ("tp", "fp", "fn")})
    precision = counts["tp"] / (counts["tp"] + counts["fp"]) if counts["tp"] + counts["fp"] else 0.0
    recall = counts["tp"] / (counts["tp"] + counts["fn"]) if counts["tp"] + counts["fn"] else 0.0
    return {"precision": precision, "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            **dict(counts)}
