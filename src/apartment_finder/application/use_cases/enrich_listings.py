"""Enrich saved ads through a prediction service, without holding database locks."""

from apartment_finder.application.ports import PoiPredictor, PoiRepository


def enrich_listings(
    repository: PoiRepository, predictor: PoiPredictor, model_version: str, limit: int
) -> int:
    """Predict POIs for up to ``limit`` pending listings and store the results.

    Returns:
        The number of predictions the repository actually saved.

    Raises:
        ValueError: If ``limit`` is below 1, or the service answers with a model version
            other than ``model_version`` (so no mislabeled predictions are stored).
    """
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
