"""Export reproducible per-listing POI predictions for visual inspection."""
import argparse
import hashlib
import json
from pathlib import Path

from training.jsonl import read_jsonl, write_jsonl
from training.token_data import LABELS, entities, name_spans, split_rows


def decode(text, offsets, tags, scores):
    positions = [i for i, (a, b) in enumerate(offsets) if a != b]
    spans = entities([LABELS[tags[i]] for i in positions])
    output = []
    for start, end, category in sorted(spans):
        indices = positions[start:end]
        a, b = offsets[indices[0]][0], offsets[indices[-1]][1]
        while a < b and text[a].isspace():
            a += 1
        output.append({"start": a, "end": b, "name": text[a:b],
                       "category": category, "confidence": round(min(scores[i] for i in indices), 5)})
    return output


def predict(text, tokenizer, model, max_length, device):
    import torch

    encoded = tokenizer(text, return_offsets_mapping=True, truncation=False)
    offsets = encoded["offset_mapping"]
    content = [i for i, (a, b) in enumerate(offsets) if a != b]
    if not content:
        return []
    first, stop = content[0], content[-1] + 1
    capacity = max_length - first - (len(offsets) - stop)
    if capacity < 1:
        raise ValueError("max_length too small for special tokens")
    predicted = [0] * len(offsets)
    scores = [0.0] * len(offsets)
    model.eval()
    for start in range(first, stop, capacity):
        end = min(start + capacity, stop)
        indices = [*range(first), *range(start, end), *range(stop, len(offsets))]
        batch = {key: torch.tensor([[values[i] for i in indices]], device=device)
                 for key, values in encoded.items() if key in ("input_ids", "attention_mask", "token_type_ids")}
        with torch.inference_mode():
            probabilities = model(**batch).logits.softmax(-1)[0]
        for local, original in enumerate(indices):
            if start <= original < end:
                scores[original], predicted[original] = (
                    probabilities[local].max().item(), probabilities[local].argmax().item())
    return decode(text, offsets, predicted, scores)


def export(run, split, output, log_mlflow=False, cpu=False):
    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer

    config = json.loads((run / "run_config.json").read_text(encoding="utf-8"))
    labels = Path(config["labels"])
    if hashlib.sha256(labels.read_bytes()).hexdigest() != config["dataset_sha256"]:
        raise ValueError("Labels have changed since training")
    splits = split_rows(read_jsonl(labels), config["validation_fraction"], config["seed"])
    manifest = json.loads((run / "split_manifest.json").read_text(encoding="utf-8"))
    current = {key: [{"id": r.get("id"), "content_hash": r.get("content_hash")} for r in rows]
               for key, rows in splits.items()}
    if current != manifest:
        raise ValueError("Split manifest mismatch")
    tokenizer = AutoTokenizer.from_pretrained(run / "model", use_fast=True)
    model = AutoModelForTokenClassification.from_pretrained(run / "model")
    if [model.config.id2label[i] for i in range(len(LABELS))] != LABELS:
        raise ValueError("Model label mapping mismatch")
    device = "cuda" if torch.cuda.is_available() and not cpu else "cpu"
    model.to(device)
    records = []
    for row in splits[split]:
        text = row["description"]
        gold = [{"start": a, "end": b, "name": text[a:b], "category": category}
                for a, b, category in name_spans(row)]
        records.append({"id": row.get("id"), "text": text, "split": split,
                        "ground_truth": gold,
                        "predictions": predict(text, tokenizer, model, config["max_length"], device)})
    write_jsonl(output, records)
    if log_mlflow:
        import mlflow
        run_info = json.loads((run / "mlflow_run.json").read_text(encoding="utf-8"))
        mlflow.set_tracking_uri(config.get("mlflow_tracking_uri", "sqlite:///mlflow.db"))
        mlflow.MlflowClient().log_artifact(run_info["run_id"], str(output), artifact_path="inspection")
    return len(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--split", choices=("validation", "gold"), default="validation")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--log-mlflow", action="store_true")
    parser.add_argument("--cpu", action="store_true")
    args = parser.parse_args()
    output = args.output or args.run / f"{args.split}_predictions.jsonl"
    print(f"Exported {export(args.run, args.split, output, args.log_mlflow, args.cpu)} rows to {output}")


if __name__ == "__main__":
    main()
