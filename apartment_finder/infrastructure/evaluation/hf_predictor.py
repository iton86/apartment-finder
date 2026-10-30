"""Hugging Face model adapter for saved POI encoder checkpoints."""
from training.inspection import predict
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
