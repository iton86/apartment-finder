"""Regression coverage for inspection adapters outside the repository root."""

import os
import subprocess
import sys
from pathlib import Path


def test_prediction_repositories_work_without_training_on_path(tmp_path):
    project_root = Path(__file__).resolve().parents[2]
    script = '''
import importlib.util
from pathlib import Path

assert importlib.util.find_spec("training") is None
from apartment_finder.infrastructure.evaluation.jsonl_repository import JsonlPredictionRepository
from apartment_finder.infrastructure.evaluation.mlflow_repository import MlflowExperimentRepository

rows = [{"id": 1, "text": "София"}]
path = Path("predictions.jsonl")
repository = JsonlPredictionRepository()
repository.save(rows, str(path))
assert repository.load(str(path)) == rows

class FakeClient:
    def download_artifacts(self, run_id, artifact_path):
        assert (run_id, artifact_path) == ("run", "inspection/predictions.jsonl")
        return str(path)

mlflow_repository = MlflowExperimentRepository("unused")
mlflow_repository._client = lambda: FakeClient()
assert mlflow_repository.load_predictions("run", "inspection/predictions.jsonl") == rows
'''
    env = {**os.environ, "PYTHONPATH": str(project_root / "src")}
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env,
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
