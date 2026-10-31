"""Compatibility imports for JSON Lines helpers used by training scripts."""

from apartment_finder.infrastructure.evaluation.jsonl import read_jsonl, write_jsonl

__all__ = ["read_jsonl", "write_jsonl"]
