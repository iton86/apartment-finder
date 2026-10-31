"""MLflow artifact adapter; import is lazy to keep the domain independent."""
from pathlib import Path

from apartment_finder.infrastructure.evaluation.jsonl import read_jsonl


class MlflowExperimentRepository:
    def __init__(self, tracking_uri: str):
        self.tracking_uri = tracking_uri

    def _client(self):
        import mlflow
        mlflow.set_tracking_uri(self.tracking_uri)
        return mlflow.MlflowClient()

    def log_predictions(self, run_id: str, file_path: str) -> None:
        self._client().log_artifact(run_id, file_path, artifact_path="inspection")

    def load_predictions(self, run_id: str, artifact_path: str) -> list[dict]:
        path = self._client().download_artifacts(run_id, artifact_path)
        return read_jsonl(Path(path))
