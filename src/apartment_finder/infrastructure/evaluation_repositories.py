"""Storage adapters for evaluation artifacts: local JSONL files and MLflow runs."""

from pathlib import Path

from apartment_finder.infrastructure.jsonl import read_jsonl, write_jsonl


class JsonlPredictionRepository:
    """Store prediction records as JSON Lines files on the local filesystem."""

    def save(self, records: list[dict], destination: str) -> None:
        """Write ``records`` to the file at ``destination``, overwriting it."""
        write_jsonl(Path(destination), records)

    def load(self, location: str) -> list[dict]:
        """Read records from the file at ``location``; a missing file yields ``[]``."""
        return read_jsonl(Path(location))


class MlflowExperimentRepository:
    """Store prediction artifacts in, and fetch them from, MLflow runs."""

    def __init__(self, tracking_uri: str):
        self.tracking_uri = tracking_uri

    def _client(self):
        import mlflow

        mlflow.set_tracking_uri(self.tracking_uri)
        return mlflow.MlflowClient()

    def log_predictions(self, run_id: str, file_path: str) -> None:
        """Attach the local file to the MLflow run under the ``inspection`` artifact folder."""
        self._client().log_artifact(run_id, file_path, artifact_path="inspection")

    def load_predictions(self, run_id: str, artifact_path: str) -> list[dict]:
        """Download a JSONL artifact from the MLflow run and return its records."""
        path = self._client().download_artifacts(run_id, artifact_path)
        return read_jsonl(Path(path))
