"""Local serving contract and optimistic listing persistence."""

import hashlib
import json
from io import BytesIO
from uuid import uuid4

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apartment_finder.application.ports.poi import PoiCandidate
from apartment_finder.application.use_cases.enrich_listings import enrich_listings
from apartment_finder.infrastructure.persistence.models import Base, ListingModel
from apartment_finder.infrastructure.poi.http_predictor import MlflowHttpPredictor
from apartment_finder.infrastructure.poi.repository import ListingPoiRepository

TEXT = "до Южен парк"
POIS = [{"name": "Южен парк", "category": "park", "start": 3, "end": 12, "confidence": 0.9}]


def prediction(text=TEXT, pois=None):
    return {
        "pois": POIS if pois is None else pois,
        "model_version": "v1",
        "description_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


def test_http_contract_and_invalid_spans(monkeypatch):
    payload = prediction()

    def respond(request, timeout):
        assert json.loads(request.data) == {"dataframe_records": [{"description": TEXT}]}
        assert timeout == 120
        return BytesIO(json.dumps({"predictions": [payload]}).encode())

    monkeypatch.setattr("apartment_finder.infrastructure.poi.http_predictor.urlopen", respond)
    client = MlflowHttpPredictor("http://127.0.0.1:5001")
    assert client.predict(TEXT) == prediction()
    payload["pois"] = [{**POIS[0], "name": "invented"}]
    with pytest.raises(ValueError, match="span"):
        client.predict(TEXT)
    payload.update(pois=[], description_sha256="wrong")
    with pytest.raises(ValueError, match="hash"):
        client.predict(TEXT)


def test_repository_skips_empty_predictions_and_handles_description_changes():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    row_id = uuid4()
    with Session(engine) as session:
        session.add(
            ListingModel(
                id=row_id,
                source="test",
                external_id="1",
                url="test",
                title="test",
                transaction_type="sale",
                city="София",
                area="",
                description=TEXT,
            )
        )
        session.commit()
    repository = ListingPoiRepository(engine)
    [candidate] = list(repository.pending("v1"))
    assert repository.save(candidate, prediction(pois=[]))
    assert list(repository.pending("v1")) == []
    assert list(repository.pending("v2")) == [candidate]
    with Session(engine) as session:
        row = session.get(ListingModel, row_id)
        assert row.pois == []
        row.description = "changed"
        session.commit()
    assert not repository.save(candidate, prediction())
    assert list(repository.pending("v1")) == [PoiCandidate(row_id, "changed")]
    engine.dispose()


def test_use_case_rejects_different_model_and_limits_work():
    class Repository:
        def pending(self, model_version):
            return [PoiCandidate(uuid4(), TEXT)] * 3

        def save(self, candidate, value):
            return True

    class Predictor:
        def predict(self, description):
            return prediction()

    assert enrich_listings(Repository(), Predictor(), "v1", 2) == 2
    with pytest.raises(ValueError, match="different model"):
        enrich_listings(Repository(), Predictor(), "v2", 1)


def test_mlflow_model_contract(monkeypatch):
    pytest.importorskip("mlflow")
    from apartment_finder.infrastructure.poi.mlflow_model import PoiModel

    class Predictor:
        def predict(self, text):
            return POIS

    model = PoiModel()
    model.predictor = Predictor()
    model.version = "v1"
    assert model.predict(None, pd.DataFrame({"description": [TEXT]})) == [prediction()]
    for value in ("", None, 4, "a" * 100001):
        with pytest.raises(ValueError):
            model.predict(None, pd.DataFrame({"description": [value]}))
