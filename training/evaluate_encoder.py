"""Evaluate a saved POI encoder on human gold rows, without updating weights."""

import argparse
import hashlib
import json
from pathlib import Path

from training.finetune import write_json
from training.jsonl import read_jsonl
from training.token_data import LABELS, compute_metrics, encode_chunks, split_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run", type=Path, help="Run directory containing model/ and run_config.json"
    )
    parser.add_argument("--cpu", action="store_true")
    args = parser.parse_args()
    from transformers import (
        AutoModelForTokenClassification,
        AutoTokenizer,
        DataCollatorForTokenClassification,
        Trainer,
        TrainingArguments,
    )

    config = json.loads((args.run / "run_config.json").read_text())
    if hashlib.sha256(Path(config["labels"]).read_bytes()).hexdigest() != config["dataset_sha256"]:
        parser.error("Dataset changed since training; restore the original labels file")
    splits = split_rows(
        read_jsonl(Path(config["labels"])), config["validation_fraction"], config["seed"]
    )
    manifest = json.loads((args.run / "split_manifest.json").read_text())
    current = {
        split: [{"id": r.get("id"), "content_hash": r.get("content_hash")} for r in rows]
        for split, rows in splits.items()
    }
    if current != manifest:
        parser.error("Dataset changed since training; restore the original labels file")
    if not splits["gold"]:
        parser.error("No human-reviewed gold rows available")
    tokenizer = AutoTokenizer.from_pretrained(args.run / "model")
    model = AutoModelForTokenClassification.from_pretrained(args.run / "model")
    if [model.config.id2label[i] for i in range(len(LABELS))] != LABELS:
        parser.error("Checkpoint label mapping does not match this POI schema")
    dataset = [
        chunk
        for row in splits["gold"]
        for chunk in encode_chunks(row, tokenizer, config["max_length"])
    ]
    trainer = Trainer(
        model=model,
        processing_class=tokenizer,
        args=TrainingArguments(
            output_dir=str(args.run),
            use_cpu=args.cpu,
            per_device_eval_batch_size=config["batch_size"],
            report_to="none",
        ),
        data_collator=DataCollatorForTokenClassification(tokenizer),
        compute_metrics=compute_metrics,
    )
    metrics = trainer.evaluate(dataset, metric_key_prefix="gold")
    write_json(args.run / "gold_metrics.json", metrics)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
