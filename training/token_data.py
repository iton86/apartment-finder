"""Convert reviewed POI names to BIO tags without leaking gold descriptions."""

import hashlib
import unicodedata

from training.dedup import cluster_near_duplicates, content_hash
from training.schema import PoiCategory, PoiExtraction, find_evidence, find_problems

LABELS = ["O", *[f"{prefix}-{category.value}" for category in PoiCategory for prefix in ("B", "I")]]
LABEL2ID = {label: i for i, label in enumerate(LABELS)}


def split_rows(rows: list[dict], validation_fraction: float, seed: int) -> dict[str, list[dict]]:
    """Keep duplicate clusters together; exclude training neighbours of gold rows."""
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be between 0 and 1")
    groups: dict[int, list[dict]] = {}
    for row in rows:
        if row.get("split") not in {"train", "gold"}:
            raise ValueError("Every row must have split=train or split=gold")
        if row.get("label_source") not in {"human", "teacher"}:
            raise ValueError("Every row must identify its human or teacher label_source")
        if not isinstance(row.get("description"), str) or not row["description"].strip():
            raise ValueError("Every row must contain a nonempty description")
        extraction = PoiExtraction.model_validate({"pois": row["pois"]})
        problems = find_problems(extraction, row["description"])
        if problems:
            raise ValueError(f"Row {row.get('id')}: {'; '.join(problems)}")
        if row["split"] == "gold" and row["label_source"] != "human":
            raise ValueError("Gold labels must be human reviewed")
    clusters = cluster_near_duplicates([r["description"] for r in rows])
    for cluster, row in zip(clusters, rows, strict=True):
        groups.setdefault(cluster, []).append(row)
    result = {"train": [], "validation": [], "gold": [], "excluded": []}
    candidates = []
    for group in groups.values():
        if any(r["split"] == "gold" for r in group):
            result["gold"].extend(r for r in group if r["split"] == "gold")
            result["excluded"].extend(r for r in group if r["split"] != "gold")
        else:
            candidates.append(group)
    # Sort by content rather than input order or unstable cluster numbers.
    candidates.sort(
        key=lambda group: hashlib.sha256(
            f"{seed}:{min(content_hash(r['description']) for r in group)}".encode()
        ).hexdigest()
    )
    if len(candidates) < 2:
        raise ValueError("Need at least two independent non-gold description clusters")
    count = min(len(candidates) - 1, max(1, round(len(candidates) * validation_fraction)))
    # Only human-reviewed clusters can select the best checkpoint.
    validation = [g for g in candidates if all(r["label_source"] == "human" for r in g)][:count]
    if not validation:
        raise ValueError("Need human-reviewed non-gold data for validation")
    for group in candidates:
        result["validation" if any(group is g for g in validation) else "train"].extend(group)
    return result


def name_spans(row: dict) -> list[tuple[int, int, str]]:
    """Locate names within evidence; reject overlaps that a BIO head cannot express."""
    spans = set()
    for poi in row["pois"]:
        evidence = find_evidence(poi["evidence"], row["description"])
        if evidence is None:
            raise ValueError("POI evidence not found in description")
        name = find_evidence(poi["name"], row["description"][evidence[0] : evidence[1]])
        if name is None:
            raise ValueError("POI name not found in evidence")
        spans.add((evidence[0] + name[0], evidence[0] + name[1], poi["category"]))
    ordered = sorted(spans)
    for left, right in zip(ordered, ordered[1:]):
        if left[1] > right[0]:
            raise ValueError(f"Row {row.get('id')}: overlapping POI names cannot use BIO tagging")
    return ordered


def encode_row(row: dict, tokenizer, max_length: int) -> dict:
    """Label every name subtoken; refuse truncation rather than lose annotations."""
    encoded = tokenizer(row["description"], return_offsets_mapping=True, truncation=False)
    if len(encoded["input_ids"]) > max_length:
        raise ValueError(
            f"Row {row.get('id')}: {len(encoded['input_ids'])} tokens exceeds max_length="
            f"{max_length}; shorten/split and review the description before training"
        )
    offsets = encoded.pop("offset_mapping")
    labels = [-100 if start == end else 0 for start, end in offsets]
    for start, end, category in name_spans(row):
        indices = [i for i, (a, b) in enumerate(offsets) if a != b and a < end and b > start]
        if not indices:
            raise ValueError("Tokenizer produced no tokens for an annotated name")
        for position, index in enumerate(indices):
            a, b = offsets[index]
            # Tokens can include edge whitespace or punctuation (e.g. "7.").
            # Permit those edges, but never absorb neighbouring word characters.
            outside = row["description"][a:start] + row["description"][end:b]
            if any(
                not char.isspace() and not unicodedata.category(char).startswith("P")
                for char in outside
            ) or labels[index] not in (0, -100):
                raise ValueError(
                    f"Row {row.get('id')}: POI {row['description'][start:end]!r} "
                    f"conflicts with token {row['description'][a:b]!r} at offsets {a}:{b}"
                )
            labels[index] = LABEL2ID[f"{'B' if position == 0 else 'I'}-{category}"]
    encoded["labels"] = labels
    return dict(encoded)


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


def compute_metrics(prediction) -> dict[str, float]:
    """Micro entity precision/recall/F1, excluding padding and special tokens."""
    predicted = prediction.predictions.argmax(axis=-1)
    correct = expected = actual = 0
    for guesses, labels in zip(predicted, prediction.label_ids, strict=True):
        pairs = [(int(p), int(y)) for p, y in zip(guesses, labels, strict=True) if y != -100]
        gold = entities([LABELS[y] for _, y in pairs])
        found = entities([LABELS[p] for p, _ in pairs])
        correct += len(gold & found)
        expected += len(gold)
        actual += len(found)
    precision = correct / actual if actual else 0.0
    recall = correct / expected if expected else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": 2 * correct / (actual + expected) if actual + expected else 0.0,
    }
