"""Compare the same frozen samples across two prediction sets."""
from apartment_finder.evaluation.domain.metrics import compare


def compare_runs(left: list[dict], right: list[dict]) -> list[dict]:
    baseline = {str(row["id"]): row for row in left}
    differences = []
    for row in right:
        original = baseline.get(str(row["id"]))
        if original is None or original["text"] != row["text"] or original["ground_truth"] != row["ground_truth"]:
            continue
        a = compare(original["ground_truth"], original["predictions"])
        b = compare(row["ground_truth"], row["predictions"])
        differences.append({"id": row["id"], "baseline_errors": a["fp"] + a["fn"],
                            "other_errors": b["fp"] + b["fn"],
                            "delta": b["fp"] + b["fn"] - a["fp"] - a["fn"]})
    return differences
