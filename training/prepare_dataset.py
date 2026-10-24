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


def prepare(
    rows: list[dict],
    *,
    seed: int = 42,
    gold_fraction: float = 0.15,
    validation_fraction: float = 0.15,
    frozen: dict | None = None,
) -> tuple[list[dict], dict[str, list[dict]], str | None]:
    """Assign gold by cluster coverage, or retain a previous gold manifest."""
    if not 0 < gold_fraction < 1:
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
    else:
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
        group_features = {
            group[0]["content_hash"]: set().union(*(features(row) for row in group))
            for group in groups
        }
        frequency = Counter(feature for values in group_features.values() for feature in values)
        covered = Counter()
        selected = []
        for _ in range(target):
            def rank(group):
                key = group[0]["content_hash"]
                gain = sum(
                    1 / (frequency[feature] * (1 + covered[feature]))
                    for feature in sorted(group_features[key])
                )
                return -gain, digest([seed, key])

            group = min(groups, key=rank)
            groups.remove(group)
            selected.extend(group)
            covered.update(group_features[group[0]["content_hash"]])
        gold = {row["content_hash"] for row in selected}
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
    validation_fraction: float = 0.15,
) -> dict:
    if output.exists():
        raise ValueError("Output already exists; use a new dataset version directory")
    frozen = json.loads(gold_manifest.read_text(encoding="utf-8")) if gold_manifest else None
    # Hash and parse the same bytes, so provenance cannot describe a different read.
    raw = source.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    labels, splits, reused = prepare(
        rows, seed=seed, gold_fraction=gold_fraction,
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
        "gold_fraction": gold_fraction,
        "gold_selection": "frozen" if frozen else "coverage_by_duplicate_cluster",
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
    parser.add_argument("--validation-fraction", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    try:
        manifest = build_snapshot(
            args.labels, args.output, gold_manifest=args.gold_manifest,
            gold_version=args.gold_version, seed=args.seed,
            gold_fraction=args.gold_fraction, validation_fraction=args.validation_fraction,
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

