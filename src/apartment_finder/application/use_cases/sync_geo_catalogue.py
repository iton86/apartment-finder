"""Import complete reference releases through application ports."""

from apartment_finder.application.ports import GeoCatalogueRepository, GeoCatalogueSource


def sync_geo_catalogue(
    source: GeoCatalogueSource, repository: GeoCatalogueRepository | None = None
) -> dict:
    """Download and validate; publish only when a repository is supplied."""
    features, manifest = source.fetch()
    if not features or not any(f.kind == "boundary" for f in features):
        raise ValueError("A catalogue must contain planning units")
    counts = {
        kind: sum(f.kind == kind for f in features) for kind in ("boundary", "poi", "surface")
    }
    release = repository.publish(features, manifest) if repository is not None else None
    return {"release": release, "counts": counts, "manifest": manifest}
