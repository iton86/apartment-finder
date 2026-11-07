"""Self-contained MLflow model; no training package or database access."""

import hashlib
import json
from pathlib import Path

import mlflow.pyfunc

from apartment_finder.infrastructure.poi.hf_predictor import HuggingFacePoiPredictor


class PoiModel(mlflow.pyfunc.PythonModel):
    """MLflow pyfunc wrapper around the Hugging Face POI predictor."""

    def load_context(self, context):
        """Load the checkpoint and model version from the packaged artifacts (CPU only)."""
        settings = json.loads(Path(context.artifacts["settings"]).read_text())
        self.version = settings["model_version"]
        self.predictor = HuggingFacePoiPredictor(
            context.artifacts["checkpoint"],
            settings["max_length"],
            cpu=True,
        )

    def predict(self, context, model_input, params=None):
        """Predict POIs for each row of the ``description`` column.

        Raises:
            ValueError: If the column is missing or the descriptions are not 1 to 100
                nonempty strings of at most 100000 characters.
        """
        if "description" not in model_input:
            raise ValueError("A description column is required")
        descriptions = model_input["description"].tolist()
        if not 1 <= len(descriptions) <= 100:
            raise ValueError("Supply between 1 and 100 descriptions")
        if any(
            not isinstance(text, str) or not text.strip() or len(text) > 100_000
            for text in descriptions
        ):
            raise ValueError("Descriptions must be nonempty strings up to 100000 characters")
        return [
            {
                "pois": self.predictor.predict(text),
                "model_version": self.version,
                "description_sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
            for text in descriptions
        ]
