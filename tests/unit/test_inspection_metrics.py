from training.inspection_metrics import compare, summarize


def test_exact_span_errors():
    gold = [{"start": 0, "end": 4, "category": "park"}]
    predicted = [{"start": 0, "end": 3, "category": "park"}]
    result = compare(gold, predicted)
    assert (result["tp"], result["fp"], result["fn"]) == (0, 1, 1)


def test_micro_metrics():
    rows = [{"ground_truth": [{"start": 0, "end": 4, "category": "park"}],
             "predictions": [{"start": 0, "end": 4, "category": "park"}]},
            {"ground_truth": [], "predictions": [{"start": 0, "end": 2, "category": "school"}]}]
    result = summarize(rows)
    assert result["precision"] == 0.5
    assert result["recall"] == 1.0
