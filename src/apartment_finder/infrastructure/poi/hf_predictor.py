"""Hugging Face model adapter for saved POI encoder checkpoints."""

from apartment_finder.domain.evaluation import entities


def decode(text, offsets, tags, scores, labels):
    """Convert per-token BIO predictions into character-level POI spans.

    Special tokens (empty offsets) are ignored, leading whitespace is trimmed,
    and each span's confidence is its weakest token score.
    """
    positions = [i for i, (a, b) in enumerate(offsets) if a != b]
    spans = entities([labels[tags[i]] for i in positions])
    output = []
    for start, end, category in sorted(spans):
        indices = positions[start:end]
        a, b = offsets[indices[0]][0], offsets[indices[-1]][1]
        while a < b and text[a].isspace():
            a += 1
        output.append(
            {
                "start": a,
                "end": b,
                "name": text[a:b],
                "category": category,
                "confidence": round(min(scores[i] for i in indices), 5),
            }
        )
    return output


def predict(text, tokenizer, model, max_length, device):
    """Extract POIs from ``text``, windowing it if it exceeds ``max_length``.

    Special tokens are kept in every window and the content is sliced into
    chunks that fit.

    Raises:
        ValueError: If ``max_length`` leaves no room for content.
    """
    labels = [model.config.id2label[i] for i in range(model.config.num_labels)]
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
        batch = {
            key: torch.tensor([[values[i] for i in indices]], device=device)
            for key, values in encoded.items()
            if key in ("input_ids", "attention_mask", "token_type_ids")
        }
        with torch.inference_mode():
            probabilities = model(**batch).logits.softmax(-1)[0]
        for local, original in enumerate(indices):
            if start <= original < end:
                scores[original], predicted[original] = (
                    probabilities[local].max().item(),
                    probabilities[local].argmax().item(),
                )
    return decode(text, offsets, predicted, scores, labels)


class HuggingFacePoiPredictor:
    """Token-classification POI predictor loaded from a saved Hugging Face checkpoint."""

    def __init__(self, model_dir, max_length: int, cpu: bool = False):
        import torch
        from transformers import AutoModelForTokenClassification, AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(model_dir, use_fast=True)
        self.model = AutoModelForTokenClassification.from_pretrained(model_dir)
        labels = [self.model.config.id2label[i] for i in range(self.model.config.num_labels)]
        if (
            not labels
            or labels[0] != "O"
            or any(not label.startswith(("B-", "I-")) for label in labels[1:])
        ):
            raise ValueError("Checkpoint does not contain a POI BIO label mapping")
        if max_length < 3:
            raise ValueError("max_length must allow content and special tokens")
        self.device = "cuda" if torch.cuda.is_available() and not cpu else "cpu"
        self.model.to(self.device)
        self.max_length = max_length

    def predict(self, text: str) -> list[dict]:
        """Return the POI spans found in ``text``."""
        return predict(text, self.tokenizer, self.model, self.max_length, self.device)
