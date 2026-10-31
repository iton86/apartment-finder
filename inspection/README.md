# POI Prediction Inspector

This dashboard reads per-sample prediction artifacts from MLflow. It does not load models during browsing.

Install from the repository root:

```bash
uv sync --group training --group tracking --group inspection
```

Train with `--mlflow` and export validation predictions:

```bash
uv run --group training --group tracking python -m training.inspection checkpoints/YOUR_RUN --split validation --log-mlflow
```

Gold is evaluation-only and is never used for checkpoint selection:

```bash
uv run --group training --group tracking python -m training.inspection checkpoints/YOUR_RUN --split gold --log-mlflow
```

Start the dashboard from the repository root:

```bash
uv run --group inspection --group tracking streamlit run inspection/app.py
```

MLflow uses `sqlite:///mlflow.db` by default. Use the same working directory and tracking URI for training, export and the UI.

The inspector reports **exact character-span + category** micro metrics. It stores the original listing text in MLflow artifacts, so do not use a remote artifact store without appropriate data controls. Predictions include a minimum token confidence across each entity; thresholding in the UI is exploratory, not a calibrated metric. Long descriptions are processed in token chunks, with the same max-length setting as training. Run comparisons require matching listing ID, text and ground truth.

## Architecture

The evaluation subsystem follows a modular Clean Architecture:

- `apartment_finder/domain/evaluation`: framework-free spans, exact matching, and metrics
- `apartment_finder/application/evaluation`: evaluation, inspection, comparison use cases and ports
- `apartment_finder/infrastructure/evaluation`: Hugging Face predictor, JSONL persistence, MLflow adapter
- `training/inspection.py`: CLI composition root; validates original dataset hash and split manifest
- `inspection/app.py`: Streamlit presentation

`training/inspection_metrics.py` is a backwards-compatible re-export of the domain metrics.

The dashboard is still a first iteration: experiment/run listing and some filtering live in Streamlit. The next refinement would move those queries to an application query service and use a dedicated experiment catalog port. Model inference is chunked; evaluate token-boundary behavior on real long descriptions before relying on the metrics for promotion.
