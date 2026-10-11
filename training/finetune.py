"""Fine-tune encoder models for POI name/category tagging; gold is evaluation-only."""

import argparse
import hashlib
import json
from pathlib import Path

from training.jsonl import read_jsonl
from training.token_data import LABEL2ID, LABELS, compute_metrics, encode_row, split_rows


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    # Keep the normal scraper and data preparation independent of the ML runtime.
    from transformers import (
        AutoModelForTokenClassification,
        AutoTokenizer,
        DataCollatorForTokenClassification,
        Trainer,
        TrainingArguments,
        set_seed,
    )

    if args.max_length < 8 or args.batch_size < 1 or args.epochs <= 0:
        raise ValueError("max-length >= 8, batch-size >= 1 and epochs > 0 are required")
    if args.learning_rate <= 0 or args.gradient_accumulation_steps < 1:
        raise ValueError("learning-rate and gradient-accumulation-steps must be positive")
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("Output directory must be empty; use a new path for each run")
    rows = read_jsonl(args.labels)
    if not rows:
        raise ValueError(f"No labeled rows found in {args.labels}")
    splits = split_rows(rows, args.validation_fraction, args.seed)
    if not args.allow_teacher and any(r["label_source"] == "teacher" for r in rows):
        raise ValueError("Teacher labels require explicit --allow-teacher")
    set_seed(args.seed)
    tokenizer = AutoTokenizer.from_pretrained(args.model, use_fast=True)
    if not tokenizer.is_fast:
        raise ValueError("A fast tokenizer with character offsets is required")
    if args.max_length > tokenizer.model_max_length:
        raise ValueError("max-length exceeds the tokenizer context limit")
    model = AutoModelForTokenClassification.from_pretrained(
        args.model,
        num_labels=len(LABELS),
        id2label=dict(enumerate(LABELS)),
        label2id=LABEL2ID,
    )
    datasets = {
        split: [encode_row(row, tokenizer, args.max_length) for row in splits[split]]
        for split in ("train", "validation")
    }
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(
        args.output / "run_config.json",
        {
            **{
                key: str(value) if isinstance(value, Path) else value
                for key, value in vars(args).items()
            },
            "dataset_sha256": hashlib.sha256(args.labels.read_bytes()).hexdigest(),
            "task": "poi_name_category_bio",
            "label_names": LABELS,
        },
    )
    write_json(
        args.output / "split_manifest.json",
        {
            split: [{"id": r.get("id"), "content_hash": r.get("content_hash")} for r in records]
            for split, records in splits.items()
        },
    )
    training_args = TrainingArguments(
        output_dir=str(args.output),
        num_train_epochs=args.epochs,
        learning_rate=args.learning_rate,
        weight_decay=0.01,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        gradient_checkpointing=args.gradient_checkpointing,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="f1",
        greater_is_better=True,
        seed=args.seed,
        data_seed=args.seed,
        use_cpu=args.cpu,
        fp16=False,
        bf16=False,
        report_to="none",
        dataloader_num_workers=0,
        logging_steps=10,
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=datasets["train"],
        eval_dataset=datasets["validation"],
        processing_class=tokenizer,
        data_collator=DataCollatorForTokenClassification(tokenizer),
        compute_metrics=compute_metrics,
    )
    trainer.train()
    trainer.save_model(str(args.output / "model"))
    tokenizer.save_pretrained(args.output / "model")
    trainer.save_metrics("validation", trainer.evaluate())
    print(f"Saved best validation checkpoint to {args.output / 'model'}")
    print("Gold data was not used for training or checkpoint selection.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=Path("data/labels.jsonl"))
    parser.add_argument("--model", default="FacebookAI/xlm-roberta-base")
    parser.add_argument("--output", type=Path, default=Path("checkpoints/poi-encoder"))
    parser.add_argument("--epochs", type=float, default=3)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=8)
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--validation-fraction", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--gradient-checkpointing", action="store_true")
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--allow-teacher", action="store_true")
    args = parser.parse_args()
    try:
        run(args)
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
