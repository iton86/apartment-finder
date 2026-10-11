"""Extract POI names/categories from text using a saved encoder checkpoint."""

import argparse
import json
from pathlib import Path

from training.token_data import LABELS, entities


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--text-file", type=Path, required=True, help="UTF-8 description")
    args = parser.parse_args()
    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer

    config = json.loads((args.run / "run_config.json").read_text())
    text = args.text_file.read_text(encoding="utf-8")
    tokenizer = AutoTokenizer.from_pretrained(args.run / "model")
    model = AutoModelForTokenClassification.from_pretrained(args.run / "model")
    if [model.config.id2label[i] for i in range(len(LABELS))] != LABELS:
        parser.error("Checkpoint label mapping does not match this POI schema")
    inputs = tokenizer(text, return_offsets_mapping=True, return_tensors="pt", truncation=False)
    offsets = inputs.pop("offset_mapping")[0].tolist()
    if len(offsets) > config["max_length"]:
        parser.error("Description exceeds the trained max-length; split it before inference")
    model.eval()
    with torch.inference_mode():
        predicted = model(**inputs).logits.argmax(-1)[0].tolist()
    positions = [i for i, (a, b) in enumerate(offsets) if a != b]
    spans = entities([LABELS[predicted[i]] for i in positions])
    results = []
    for start, end, category in sorted(spans):
        a, b = offsets[positions[start]][0], offsets[positions[end - 1]][1]
        while a < b and text[a].isspace():
            a += 1
        results.append({"name": text[a:b], "category": category, "start": a, "end": b})
    print(json.dumps({"entities": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
