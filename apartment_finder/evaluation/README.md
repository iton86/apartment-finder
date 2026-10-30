# Evaluation module

Purpose: reproducible model inference and exact character-span POI scoring. No UI dependencies.

- `domain/`: entity spans, matching and micro metrics
- `application/`: evaluation use case and persistence/predictor ports
- `infrastructure/`: Hugging Face, JSONL, MLflow adapters
- `interface/cli.py`: export command, dataset hash and split manifest verification

```bash
uv run --group training --group tracking python -m apartment_finder.evaluation.interface.cli checkpoints/YOUR_RUN --split validation --log-mlflow
```

The frozen gold split is supported via `--split gold`; it is never used for checkpoint selection.
