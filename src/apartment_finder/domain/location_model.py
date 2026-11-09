"""Small listwise logistic ranker with held-out temperature calibration and release gates."""

import math
from collections import Counter
from datetime import datetime

from apartment_finder.domain.geo import fingerprint
from apartment_finder.domain.location_resolution import FEATURE_NAMES, RESOLVER_VERSION

UNKNOWN = [0.0] * (len(FEATURE_NAMES) - 1) + [1.0]


def _softmax(values):
    peak = max(values)
    terms = [math.exp(value - peak) for value in values]
    total = sum(terms)
    return [term / total for term in terms]


def probabilities(candidates: list[dict], model: dict) -> list[tuple[str | None, float]]:
    """Keep explicit unknown mass rather than normalizing only generated units."""
    vectors = [row["features"] for row in candidates] + [UNKNOWN]
    logits = [
        sum(w * x for w, x in zip(model["weights"], vector, strict=True)) / model["temperature"]
        for vector in vectors
    ]
    ids = [row["planning_unit_id"] for row in candidates] + [None]
    return sorted(zip(ids, _softmax(logits), strict=True), key=lambda row: -row[1])


def wilson_lower(correct: int, count: int) -> float:
    """Conservative 95% lower confidence bound on accepted-set precision."""
    if not count:
        return 0.0
    z = 1.96
    p = correct / count
    return (
        p + z * z / (2 * count) - z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count))
    ) / (1 + z * z / count)


def evaluate_model(examples: list[dict], model: dict) -> dict:
    """Measure held-out coverage/precision and top-label Brier score."""
    accepted = correct = 0
    brier = 0.0
    for example in examples:
        result = example["result"]
        ranked = probabilities(result["candidates"], model)
        top, probability = ranked[0]
        runner = ranked[1][1] if len(ranked) > 1 else 0.0
        right = top == example["planning_unit_id"]
        brier += (probability - float(right)) ** 2
        if (
            top is not None
            and probability >= 0.9
            and probability - runner >= 0.15
            and not result["conflict"]
        ):
            accepted += 1
            correct += right
    return {
        "examples": len(examples),
        "accepted": accepted,
        "correct": correct,
        "precision": correct / accepted if accepted else None,
        "coverage": accepted / len(examples) if examples else 0.0,
        "precision_lower_95": wilson_lower(correct, accepted),
        "top_label_brier": brier / len(examples) if examples else None,
    }


def model_ready(model: dict) -> bool:
    """Enable map acceptance only after a sufficient independent held-out evaluation."""
    metrics = model["test_metrics"]
    return (
        model["split_counts"]["train"] >= 40
        and model["split_counts"]["calibration"] >= 20
        and metrics["examples"] >= 40
        and metrics["accepted"] >= 35
        and wilson_lower(metrics["correct"], metrics["accepted"]) >= 0.9
    )


def train_location_model(examples: list[dict], context: dict) -> dict:
    """Train on review groups, calibrate on a separate split and report untouched test metrics.

    Explicit grouping/splits are supplied by reviewers; no duplicate physical
    property group can appear in more than one split. No external ML dependency.
    """
    seen_groups, listings, rows = (
        {},
        set(),
        {split: [] for split in ("train", "calibration", "test")},
    )
    description_splits, geography_splits, unit_splits = {}, {}, {}
    examples = [
        row
        | {"group_id": row["group_id"].strip(), "geography_group": row["geography_group"].strip()}
        for row in examples
    ]
    for example in examples:
        split, group = example["split"], example["group_id"].strip()
        if split not in rows or not group:
            raise ValueError("Reviewed examples require explicit train/calibration/test groups")
        if group in seen_groups and seen_groups[group] != split:
            raise ValueError("Property group leakage between splits")
        seen_groups[group] = split
        text_hash = example["description_sha256"]
        if text_hash in description_splits and description_splits[text_hash] != split:
            raise ValueError("Duplicate description leakage between splits")
        description_splits[text_hash] = split
        listing = example["listing_id"]
        if listing in listings:
            raise ValueError("A listing cannot appear twice in model examples")
        listings.add(listing)
        geography = example["geography_group"]
        unit = example["planning_unit_id"]
        if not geography:
            raise ValueError("Geographic holdout group is required")
        if geography in geography_splits and geography_splits[geography] != split:
            raise ValueError("Geographic group leakage between splits")
        geography_splits[geography] = split
        if unit is not None:
            if unit in unit_splits and unit_splits[unit] != split:
                raise ValueError("Planning-unit geographic leakage between splits")
            unit_splits[unit] = split
        observed = datetime.fromisoformat(example["observed_at"])
        if observed.tzinfo is None:
            raise ValueError("Observed timestamps must include timezone")
        for candidate in example["result"]["candidates"]:
            vector = candidate["features"]
            if (
                not isinstance(vector, list)
                or len(vector) != len(FEATURE_NAMES)
                or any(
                    type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1
                    for x in vector
                )
            ):
                raise ValueError("Invalid training feature vector")
        rows[split].append(example)
    counts = Counter(seen_groups.values())
    if counts["train"] < 40 or counts["calibration"] < 20 or counts["test"] < 40:
        raise ValueError("Need at least 40 train, 20 calibration and 40 test property groups")
    # One example per physical-property group prevents repeated ads dominating training/evaluation.
    for split in rows:
        unique = {}
        for example in sorted(rows[split], key=lambda e: e["listing_id"]):
            unique.setdefault(example["group_id"], example)
        rows[split] = list(unique.values())
    for split, split_rows in rows.items():
        unknown = sum(row["planning_unit_id"] is None for row in split_rows)
        if unknown < 5 or len(split_rows) - unknown < 5:
            raise ValueError(
                f"{split} requires at least five known and five unknown/outside groups"
            )
    times = {
        split: [datetime.fromisoformat(row["observed_at"]) for row in split_rows]
        for split, split_rows in rows.items()
    }
    if not (
        max(times["train"]) < min(times["calibration"])
        and max(times["calibration"]) < min(times["test"])
    ):
        raise ValueError("Chronological holdouts must follow train, calibration, test order")
    weights = [0.0] * len(FEATURE_NAMES)
    for _ in range(600):
        gradient = [0.0] * len(weights)
        for example in rows["train"]:
            candidates = example["result"]["candidates"]
            vectors = [c["features"] for c in candidates] + [UNKNOWN]
            targets = [c["planning_unit_id"] for c in candidates] + [None]
            target = example["planning_unit_id"]
            target_index = targets.index(target) if target in targets else len(vectors) - 1
            probs = _softmax([sum(w * x for w, x in zip(weights, v, strict=True)) for v in vectors])
            for i, vector in enumerate(vectors):
                for j, value in enumerate(vector):
                    gradient[j] += (probs[i] - (i == target_index)) * value
        for j in range(len(weights)):
            weights[j] -= 0.15 * (gradient[j] / len(rows["train"]) + 0.002 * weights[j])
    model = {
        "schema_version": 1,
        "resolver_version": RESOLVER_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "weights": weights,
        "temperature": 1.0,
        "context": context,
        "split_counts": dict(counts),
    }

    def loss(temperature):
        model["temperature"] = temperature
        total = 0.0
        for example in rows["calibration"]:
            ranked = probabilities(example["result"]["candidates"], model)
            target = example["planning_unit_id"]
            if target not in {unit for unit, _ in ranked}:
                target = None
            total -= math.log(max(1e-12, dict(ranked)[target]))
        return total

    model["temperature"] = min((0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0), key=loss)
    model["test_metrics"] = evaluate_model(rows["test"], model)
    model["training_fingerprint"] = fingerprint(examples)
    model["version"] = fingerprint(model)
    return model


def validate_model(model: dict, context: dict) -> None:
    """Reject incompatible, malformed or tampered local calibration artifacts."""
    if (
        model.get("resolver_version") != RESOLVER_VERSION
        or model.get("feature_names") != list(FEATURE_NAMES)
        or model.get("context") != context
    ):
        raise ValueError("Model does not match resolver, catalogue, references and policy")
    weights = model.get("weights")
    if (
        not isinstance(weights, list)
        or len(weights) != len(FEATURE_NAMES)
        or any(type(w) not in (int, float) or not math.isfinite(w) for w in weights)
        or type(model.get("temperature")) not in (int, float)
        or not math.isfinite(model["temperature"])
        or not 0.1 <= model["temperature"] <= 10
    ):
        raise ValueError("Invalid model coefficients or temperature")
    if model.get("version") != fingerprint({k: v for k, v in model.items() if k != "version"}):
        raise ValueError("Model artifact fingerprint mismatch")
    metrics = model.get("test_metrics", {})
    if any(
        type(metrics.get(k)) is not int or metrics[k] < 0
        for k in ("examples", "accepted", "correct")
    ):
        raise ValueError("Invalid held-out metrics")
    if not 0 <= metrics["correct"] <= metrics["accepted"] <= metrics["examples"]:
        raise ValueError("Inconsistent held-out metrics")
