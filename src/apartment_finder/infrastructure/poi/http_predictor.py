"""Validated client for MLflow's local /invocations endpoint."""

import hashlib
import json
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


class Poi(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: StrictStr = Field(min_length=1)
    category: StrictStr = Field(pattern=r"^[a-z][a-z_]*$")
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)
    confidence: float = Field(ge=0, le=1)


class Prediction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pois: list[Poi]
    model_version: StrictStr = Field(min_length=1)
    description_sha256: StrictStr


class MlflowHttpPredictor:
    def __init__(self, url: str, timeout: float = 120):
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Prediction URL must be HTTP or HTTPS")
        self.url = url.rstrip("/") + "/invocations"
        self.timeout = timeout

    def predict(self, description: str) -> dict:
        request = Request(
            self.url,
            data=json.dumps({"dataframe_records": [{"description": description}]}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
            data = response.read(10_000_001)
        if len(data) > 10_000_000:
            raise ValueError("Prediction response exceeds size limit")
        rows = json.loads(data).get("predictions")
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError("Expected exactly one prediction")
        prediction = Prediction.model_validate(rows[0])
        if prediction.description_sha256 != hashlib.sha256(description.encode()).hexdigest():
            raise ValueError("Prediction description hash mismatch")
        for poi in prediction.pois:
            if not poi.start < poi.end <= len(description):
                raise ValueError("Invalid POI offsets")
            if description[poi.start : poi.end] != poi.name:
                raise ValueError("POI name does not match its description span")
        return prediction.model_dump()
