"""Export reproducible per-listing POI predictions for visual inspection."""

import argparse
import hashlib
import json
from pathlib import Path

from apartment_finder.application.use_cases.evaluation import EvaluateModel
from apartment_finder.infrastructure.evaluation_repositories import (
    JsonlPredictionRepository,
    MlflowExperimentRepository,
)
from apartment_finder.infrastructure.jsonl import read_jsonl
from apartment_finder.infrastructure.poi.hf_predictor import HuggingFacePoiPredictor
from training.token_data import name_spans, split_rows


def export(run, split, output, log_mlflow=False, cpu=False):
    """Predict on one split of a trained run and write predictions for inspection.

    Verifies the labels file and split manifest still match the run, then optionally
    logs the output to MLflow.

    Returns:
        Number of rows predicted.

    Raises:
        ValueError: If the labels or split membership changed since training.
    """
    config = json.loads((run / "run_config.json").read_text(encoding="utf-8"))
    labels = Path(config["labels"])
    if hashlib.sha256(labels.read_bytes()).hexdigest() != config["dataset_sha256"]:
        raise ValueError("Labels have changed since training")
    splits = split_rows(read_jsonl(labels), config["validation_fraction"], config["seed"])
    manifest = json.loads((run / "split_manifest.json").read_text(encoding="utf-8"))
    current = {
        key: [{"id": r.get("id"), "content_hash": r.get("content_hash")} for r in rows]
        for key, rows in splits.items()
    }
    if current != manifest:
        raise ValueError("Split manifest mismatch")
    predictor = HuggingFacePoiPredictor(run / "model", config["max_length"], cpu=cpu)
    rows = []
    for row in splits[split]:
        text = row["description"]
        gold = [
            {"start": start, "end": end, "name": text[start:end], "category": category}
            for start, end, category in name_spans(row)
        ]
        rows.append({"id": row.get("id"), "text": text, "split": split, "ground_truth": gold})
    EvaluateModel(predictor, JsonlPredictionRepository()).execute(rows, str(output))
    if log_mlflow:
        run_info = json.loads((run / "mlflow_run.json").read_text(encoding="utf-8"))
        MlflowExperimentRepository("sqlite:///mlflow.db").log_predictions(
            run_info["run_id"], str(output)
        )
    return len(rows)


def main():
    """Command-line entry point: export predictions and print the row count."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--split", choices=("validation", "gold"), default="validation")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--log-mlflow", action="store_true")
    parser.add_argument("--cpu", action="store_true")
    args = parser.parse_args()
    output = args.output or args.run / f"{args.split}_predictions.jsonl"
    count = export(args.run, args.split, output, args.log_mlflow, args.cpu)
    print(f"Exported {count} rows to {output}")


if __name__ == "__main__":
    main()
