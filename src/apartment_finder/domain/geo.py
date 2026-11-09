"""Validated WGS84 reference features, independent of storage and providers."""

import hashlib
import json
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class GeoFeature:
    """One source feature in an immutable catalogue release."""

    dataset: str
    external_id: str
    kind: str
    name: str | None
    category: str | None
    geometry: dict
    attributes: dict


def fingerprint(value: object) -> str:
    """Hash JSON deterministically; reject nonfinite numbers."""
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def validate_geometry(geometry: dict, kind: str) -> dict:
    """Validate source shape and Sofia bounds; promote polygons to multipolygons.

    Topological validation additionally runs in PostGIS before publication.
    """
    expected = {
        "boundary": {"Polygon", "MultiPolygon"},
        "poi": {"Point"},
        "surface": {"Polygon", "MultiPolygon"},
    }
    if kind not in expected or geometry.get("type") not in expected[kind]:
        raise ValueError("Unexpected geometry type")
    shape = geometry["type"]
    coordinates = geometry.get("coordinates")

    def point(value):
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError("Expected a two-dimensional coordinate")
        if any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) for v in value
        ):
            raise ValueError("Coordinates must be finite numbers")
        if not (22.5 <= value[0] <= 24.35 and 42.3 <= value[1] <= 43):
            raise ValueError("Coordinates outside Sofia WGS84 bounds")

    def polygon(rings):
        if not isinstance(rings, list) or not rings:
            raise ValueError("Empty polygon")
        for ring in rings:
            if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("Polygon rings must be closed with at least four coordinates")
            for coordinate in ring:
                point(coordinate)

    if shape == "Point":
        point(coordinates)
    elif shape == "Polygon":
        polygon(coordinates)
        return {"type": "MultiPolygon", "coordinates": [coordinates]}
    else:
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError("Empty multipolygon")
        for part in coordinates:
            polygon(part)
    return {"type": shape, "coordinates": coordinates}
