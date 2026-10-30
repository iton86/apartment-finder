"""Read-only MLflow adapter for inspection queries."""
from apartment_finder.evaluation.infrastructure.mlflow_repository import MlflowExperimentRepository


class MlflowInspectionReader:
    def __init__(self, tracking_uri: str):
        self._repository = MlflowExperimentRepository(tracking_uri)

    def load_predictions(self, run_id: str, artifact_path: str) -> list[dict]:
        return self._repository.load_predictions(run_id, artifact_path)

    def load(self, location: str) -> list[dict]:
        run_id, artifact_path = location.split(":", 1)
        return self.load_predictions(run_id, artifact_path)

    def experiments(self):
        return self._repository._client().search_experiments()

    def runs(self, experiment_id: str):
        return self._repository._client().search_runs(
            [experiment_id], max_results=200, order_by=["attributes.start_time DESC"]
        )

    def artifacts(self, run_id: str):
        return self._repository._client().list_artifacts(run_id, "inspection")
