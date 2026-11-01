"""Enrich saved ads through a prediction service, without holding database locks."""

from apartment_finder.application.ports.poi import PoiPredictor, PoiRepository


def enrich_listings(
    repository: PoiRepository, predictor: PoiPredictor, model_version: str, limit: int
) -> int:
    if limit < 1:
        raise ValueError("limit must be positive")
    saved = 0
    for index, candidate in enumerate(repository.pending(model_version)):
        if index >= limit:
            break
        prediction = predictor.predict(candidate.description)
        if prediction["model_version"] != model_version:
            raise ValueError("The service is serving a different model version")
        saved += repository.save(candidate, prediction)
    return saved
