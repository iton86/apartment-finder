"""Build immutable, duplicate-aware dataset snapshots from approved labels."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from training.dedup import cluster_near_duplicates
from training.jsonl import write_jsonl
from training.schema import PoiExtraction, find_problems
from training.token_data import split_rows

FORMAT_VERSION = 1


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def label_fingerprint(row: dict) -> str:
    # Approval timestamps may change without changing the benchmark's labels.
    return digest({
        "description": row["description"],
        "pois": sorted(row["pois"], key=lambda poi: json.dumps(poi, sort_keys=True)),
    })


def features(row: dict) -> set[str]:
    values = {f"category:{poi['category']}" for poi in row["pois"]}
    if not row["pois"]:
        values.add("no_pois")
    if len(row["description"]) >= 1500:
        values.add("long_description")
    for key in ("city", "area"):
        if row.get(key):
            values.add(f"{key}:{row[key]}")
    return values


def coverage(rows: list[dict]) -> dict:
    counts = Counter(feature for row in rows for feature in features(row))
    return {"rows": len(rows), "features": dict(sorted(counts.items()))}


def poi_types(row: dict) -> set[str]:
    """Count each type once per record, including a bucket for negative ads."""
    return {poi["category"] for poi in row["pois"]} or {"no_pois"}


def select_gold(groups: list[list[dict]], target: int, seed: int, *, exact: bool) -> set[str]:
    """Greedily balance record coverage of POI types, with deterministic ties.

    Exact mode selects individual records and lets split_rows exclude their
    duplicate neighbours. Fraction mode selects entire duplicate clusters.
    Always leave two untouched clusters for training and validation.
    """
    sizes = sorted(len(group) for group in groups)
    total = sum(sizes)
    if exact and target > total - sum(sizes[:2]):
        raise ValueError("gold-count leaves fewer than two independent non-gold clusters")
    candidates = [
        (index, [row] if exact else group)
        for index, group in enumerate(groups)
        for row in (group if exact else group[:1])
    ]
    counts = Counter()
    types = {row["content_hash"]: poi_types(row) for group in groups for row in group}
    selected: set[str] = set()
    touched: set[int] = set()
    for _ in range(target):
        untouched = sorted(
            (len(group), i) for i, group in enumerate(groups) if i not in touched
        )

        def feasible(candidate):
            index, _records = candidate
            if len(untouched) - (index not in touched) < 2:
                return False
            # Reserving the two smallest untouched clusters must still allow
            # enough records to reach the requested total. Only the three
            # smallest clusters can affect this reservation.
            reserved = [size for size, i in untouched[:3] if i != index][:2]
            return not exact or target <= total - sum(reserved)

        def rank(candidate):
            _index, records = candidate
            gain = sum(
                sum(1 / (1 + counts[kind]) for kind in sorted(types[row["content_hash"]]))
                / len(types[row["content_hash"]])
                for row in records
            ) / len(records)
            return -gain, digest([seed, records[0]["content_hash"]])

        candidate = min((item for item in candidates if feasible(item)), key=rank)
        index, records = candidate
        candidates.remove(candidate)
        touched.add(index)
        for row in records:
            selected.add(row["content_hash"])
            counts.update(types[row["content_hash"]])
    return selected


def prepare(
    rows: list[dict],
    *,
    seed: int = 42,
    gold_fraction: float = 0.15,
    gold_count: int | None = None,
    validation_fraction: float = 0.15,
    frozen: dict | None = None,
) -> tuple[list[dict], dict[str, list[dict]], str | None]:
    """Balance gold by POI type, or retain a previous gold manifest."""
    if gold_count is not None and (type(gold_count) is not int or gold_count < 1):
        raise ValueError("gold-count must be a positive integer")
    if gold_count is None and not 0 < gold_fraction < 1:
        raise ValueError("gold-fraction must be between 0 and 1")
    if not rows:
        raise ValueError("No approved labels found")
    seen = set()
    ordered = []
    for source in rows:
        row = dict(source)
        key = row.get("content_hash")
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError("Each row needs a unique, nonempty content_hash")
        seen.add(key)
        if row.get("label_source") != "human":
            raise ValueError("Dataset preparation requires human-reviewed labels")
        if not isinstance(row.get("description"), str) or not row["description"].strip():
            raise ValueError(f"Row {key}: missing description")
        extraction = PoiExtraction.model_validate({"pois": row.get("pois")})
        problems = find_problems(extraction, row["description"])
        if problems:
            raise ValueError(f"Row {key}: {'; '.join(problems)}")
        ordered.append(row)
    ordered.sort(key=lambda row: row["content_hash"])
    by_hash = {row["content_hash"]: row for row in ordered}
    reused_version = None
    if frozen is not None:
        if frozen.get("format_version") != FORMAT_VERSION:
            raise ValueError("Unsupported frozen manifest format")
        if not frozen.get("gold_rows") or not frozen.get("gold_version"):
            raise ValueError("Frozen manifest must identify a nonempty gold benchmark")
        reused_version = frozen["gold_version"]
        gold = set()
        for member in frozen["gold_rows"]:
            key = member["content_hash"]
            if key not in by_hash:
                raise ValueError(f"Frozen gold row missing from input: {key}")
            if label_fingerprint(by_hash[key]) != member["label_fingerprint"]:
                raise ValueError(f"Frozen gold labels changed: {key}; create a new gold version")
            gold.add(key)
    if frozen is None or gold_count is not None:
        reused_version = None
        clusters: dict[int, list[dict]] = {}
        for cluster, row in zip(
            cluster_near_duplicates([row["description"] for row in ordered]), ordered, strict=True
        ):
            clusters.setdefault(cluster, []).append(row)
        groups = list(clusters.values())
        if len(groups) < 3:
            raise ValueError("Need at least three independent clusters for train/validation/gold")
        # Fraction is of independent clusters, not individual duplicate ads.
        target = min(len(groups) - 2, max(1, round(len(groups) * gold_fraction)))
        gold = select_gold(
            groups, gold_count if gold_count is not None else target,
            seed, exact=gold_count is not None,
        )
    for row in ordered:
        row["split"] = "gold" if row["content_hash"] in gold else "train"
    # Reuse the trainer's exact duplicate isolation and validation selection.
    splits = split_rows(ordered, validation_fraction, seed)
    if not splits["train"] or not splits["validation"] or not splits["gold"]:
        raise ValueError("Need nonempty train, validation, and gold splits")
    return ordered, splits, reused_version


def build_snapshot(
    source: Path,
    output: Path,
    *,
    gold_manifest: Path | None = None,
    gold_version: str | None = None,
    seed: int = 42,
    gold_fraction: float = 0.15,
    gold_count: int | None = None,
    validation_fraction: float = 0.15,
) -> dict:
    if output.exists():
        raise ValueError("Output already exists; use a new dataset version directory")
    frozen = json.loads(gold_manifest.read_text(encoding="utf-8")) if gold_manifest else None
    if frozen is not None and gold_count is not None:
        if not gold_version or gold_version == frozen.get("gold_version"):
            raise ValueError("Rebalancing frozen gold requires a new --gold-version")
    # Hash and parse the same bytes, so provenance cannot describe a different read.
    raw = source.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    labels, splits, reused = prepare(
        rows, seed=seed, gold_fraction=gold_fraction, gold_count=gold_count,
        validation_fraction=validation_fraction, frozen=frozen,
    )
    if reused and gold_version and gold_version != reused:
        raise ValueError("Cannot rename a reused frozen gold benchmark")
    version = reused or gold_version or output.name
    output.mkdir(parents=True)
    files = {}
    for name, records in {"labels": labels, **splits}.items():
        path = output / f"{name}.jsonl"
        write_jsonl(path, records)
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "format_version": FORMAT_VERSION,
        "dataset_version": output.name,
        "gold_version": version,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "schema_sha256": digest(PoiExtraction.model_json_schema()),
        "source": str(source),
        "seed": seed,
        "gold_fraction": gold_fraction if gold_count is None else None,
        "gold_count": gold_count,
        "gold_selection": "frozen" if reused else "balanced_by_poi_type",
        "validation_fraction": validation_fraction,
        "duplicate_threshold": 0.8,
        "files": files,
        "coverage": {name: coverage(records) for name, records in splits.items()},
        "gold_rows": [
            {"content_hash": row["content_hash"], "label_fingerprint": label_fingerprint(row)}
            for row in splits["gold"]
        ],
        "splits": {
            name: [
                {
                    key: row.get(key)
                    for key in ("id", "content_hash", "approved_revision", "approved_at")
                }
                for row in records
            ]
            for name, records in splits.items()
        },
        "warnings": [],
    }
    gold_features = manifest["coverage"]["gold"]["features"]
    all_features = set().union(*(features(row) for row in labels))
    focus = {"category:street", "category:market", "no_pois", "long_description"}
    for feature in sorted(all_features | focus):
        if feature not in all_features:
            manifest["warnings"].append(f"Dataset has no examples of {feature}")
        elif feature not in gold_features:
            manifest["warnings"].append(f"Gold has no examples of {feature}")
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True, help="Approved JSONL export")
    parser.add_argument("--output", type=Path, required=True, help="New dataset version directory")
    parser.add_argument("--gold-manifest", type=Path, help="Reuse gold from a previous manifest")
    parser.add_argument("--gold-version", help="Name a new gold benchmark, e.g. gold-v1")
    parser.add_argument("--gold-fraction", type=float, default=0.15)
    parser.add_argument(
        "--gold-count", type=int,
        help="Total gold records, balanced by POI type; overrides --gold-fraction. "
        "With --gold-manifest, reselect gold and require a new --gold-version.",
    )
    parser.add_argument("--validation-fraction", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    try:
        manifest = build_snapshot(
            args.labels, args.output, gold_manifest=args.gold_manifest,
            gold_version=args.gold_version, seed=args.seed,
            gold_fraction=args.gold_fraction, gold_count=args.gold_count,
            validation_fraction=args.validation_fraction,
        )
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))
    print(f"Created {args.output}; gold benchmark: {manifest['gold_version']}")
    for name, report in manifest["coverage"].items():
        print(f"{name}: {report['rows']} rows")
    for warning in manifest["warnings"]:
        print(f"Warning: {warning}")
    print(
        "Train with: uv run --group training python -m training.finetune "
        f"--labels {args.output / 'labels.jsonl'} "
        f"--seed {args.seed} --validation-fraction {args.validation_fraction} "
        f"--output checkpoints/{args.output.name}"
    )


if __name__ == "__main__":
    main()

