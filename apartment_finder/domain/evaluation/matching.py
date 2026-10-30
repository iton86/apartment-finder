"""Exact character-offset and category matching."""
from apartment_finder.domain.evaluation.entities import EntitySpan


def match(gold: list[EntitySpan], predicted: list[EntitySpan]) -> dict:
    expected = {span.identity() for span in gold}
    actual = {span.identity() for span in predicted}
    correct = expected & actual
    return {"tp": len(correct), "fp": len(actual - correct), "fn": len(expected - correct),
            "errors": [
                *({"type": "FN", "span": span} for span in sorted(expected - correct)),
                *({"type": "FP", "span": span} for span in sorted(actual - correct)),
            ]}
