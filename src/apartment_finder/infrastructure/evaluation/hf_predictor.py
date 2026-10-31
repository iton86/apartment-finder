"""Hugging Face model adapter for saved POI encoder checkpoints."""
from training.token_data import entities

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



from training.token_data import LABELS


class HuggingFacePoiPredictor:
    def __init__(self, model_dir, max_length: int, cpu: bool = False):
        import torch
        from transformers import AutoModelForTokenClassification, AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(model_dir, use_fast=True)
        self.model = AutoModelForTokenClassification.from_pretrained(model_dir)
        if [self.model.config.id2label[i] for i in range(len(LABELS))] != LABELS:
            raise ValueError("Checkpoint label mapping mismatch")
        self.device = "cuda" if torch.cuda.is_available() and not cpu else "cpu"
        self.model.to(self.device)
        self.max_length = max_length

    def predict(self, text: str) -> list[dict]:
        return predict(text, self.tokenizer, self.model, self.max_length, self.device)
