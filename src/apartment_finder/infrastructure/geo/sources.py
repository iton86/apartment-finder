"""Complete planning-unit, ArcGIS POI and official street-surface extraction."""

import io
import json
import zipfile
from datetime import UTC, datetime
from urllib.parse import urlencode

from apartment_finder.domain.geo import GeoFeature, validate_geometry
from apartment_finder.infrastructure.geo.download import SnapshotDownloader

BOUNDARIES = "https://ge.sofiaplan.bg/api.php?request=get_city_units"
POI_SERVICE = "https://gis.sofiaplan.bg/server/rest/services/poi/poi/MapServer"
STREETS_PACKAGE = "https://urbandata.sofia.bg/api/3/action/package_show?id=streets_curbs-zip"
LAYERS = (1, 4, 7, 10, 13, 16, 19)
MAPPING_VERSION = "sofiaplan-v1"
# Explicit reviewed code/label pairs. All other source records remain unmapped.
CATEGORY_RULES = {
    (1, "1.1.1", "Аерогара"): "airport",
    (4, "2.1.1", "Градски паркове"): "park",
    (4, "2.1.2", "Градски градини"): "park",
    (4, "2.1.3", "Квартални градини/ паркове"): "park",
    (4, "2.3.2", "Фитнес зали"): "gym",
    (10, "4.4", "Банки и банкови клонове"): "bank",
    (13, "5.2.2", "Основно училище и Начално училище"): "school",
    (13, "5.2.3", "Средно общообразователно чилище"): "school",
    (13, "5.2.4", "Профилирани, професионални гимназии и специални училища"): "school",
    (13, "5.4.2", "Детски градини и детски градини с яслени групи"): "kindergarten",
    (13, "5.4.3", "Частни детски градини и детски градини с яслени групи"): "kindergarten",
    (13, "5.1.3", "Болници"): "hospital",
    (13, "5.1.1", "Поликлиники"): "medical_center",
    (13, "5.1.2", "Здравни центрове/ медицински центрове"): "medical_center",
    (16, "6.1", "Университети (вкл. колежи и полувисши училища)"): "university",
    (19, "7.4.5", "Търговски комплекси, молове"): "shopping_mall",
    (19, "7.4.2", "Супермаркети"): "supermarket",
    (19, "7.4.4", "Пазари"): "market",
    (19, "7.5.1", "Ресторанти"): "restaurant",
}


def clean_name(value) -> str | None:
    """Exclude placeholders from matching without modifying source attributes."""
    if not isinstance(value, str) or value.strip() in {"", "---"}:
        return None
    return value.strip()


def geojson_features(data: dict, dataset: str, kind: str) -> list[GeoFeature]:
    """Validate a complete GeoJSON response, rejecting duplicate identities."""
    if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        raise ValueError("Expected a GeoJSON FeatureCollection")
    if data.get("crs"):
        raise ValueError("Expected WGS84 GeoJSON without an explicit legacy CRS")
    result, seen = [], set()
    for feature in data["features"]:
        attrs = feature["properties"]
        external_id = attrs.get("id" if kind == "boundary" else "rn")
        if isinstance(external_id, bool) or not isinstance(external_id, (str, int)):
            raise ValueError("Missing feature identity")
        external_id = str(external_id).strip()
        if not external_id or external_id in seen:
            raise ValueError("Missing or duplicate feature identity")
        seen.add(external_id)
        name = clean_name(attrs.get("name" if kind == "boundary" else "street_name"))
        if kind == "boundary" and name is None:
            raise ValueError("Planning units must have names")
        result.append(
            GeoFeature(
                dataset,
                external_id,
                kind,
                name,
                None,
                validate_geometry(feature["geometry"], kind),
                attrs,
            )
        )
    if not result:
        raise ValueError("Empty reference dataset")
    return result


class SofiaPlanCatalogueSource:
    """Fetch all required sources, retaining raw responses before normalization."""

    def __init__(self, downloader: SnapshotDownloader, expected_boundaries: int = 564):
        if expected_boundaries < 1:
            raise ValueError("Expected boundary count must be positive")
        self.downloader = downloader
        self.expected_boundaries = expected_boundaries

    def fetch(self) -> tuple[list[GeoFeature], dict]:
        """Fetch a complete release; any source failure prevents publication."""
        features = geojson_features(self.downloader.json(BOUNDARIES), "planning_units", "boundary")
        if len(features) != self.expected_boundaries:
            raise ValueError("Planning-unit count changed; review before importing")
        for layer in LAYERS:
            features.extend(self._pois(layer))
        features.extend(self._surfaces())
        manifest = {
            "mapping_version": MAPPING_VERSION,
            "fetched_at": datetime.now(UTC).isoformat(),
            "snapshots": dict(self.downloader.records),
            "consistency": "Full snapshots; provider does not guarantee cross-source atomicity",
        }
        return features, manifest

    def _query(self, layer: int, **parameters):
        return self.downloader.json(
            f"{POI_SERVICE}/{layer}/query?" + urlencode(parameters | {"f": "json"})
        )

    def _ids(self, layer: int) -> set[int]:
        data = self._query(layer, where="1=1", returnIdsOnly="true")
        ids = data.get("objectIds")
        if (
            not isinstance(ids, list)
            or not ids
            or any(type(i) is not int for i in ids)
            or len(ids) != len(set(ids))
        ):
            raise ValueError("Invalid ArcGIS object ID enumeration")
        return set(ids)

    def _pois(self, layer: int) -> list[GeoFeature]:
        metadata = self.downloader.json(f"{POI_SERVICE}/{layer}?f=json")
        oid_fields = [
            f["name"] for f in metadata.get("fields", []) if f.get("type") == "esriFieldTypeOID"
        ]
        required = {"idfkod", "function", "tradename"}
        if (
            len(oid_fields) != 1
            or metadata.get("geometryType") != "esriGeometryPoint"
            or not required.issubset({f["name"] for f in metadata.get("fields", [])})
        ):
            raise ValueError("ArcGIS layer schema changed")
        ids = self._ids(layer)
        if self._query(layer, where="1=1", returnCountOnly="true").get("count") != len(ids):
            raise ValueError("ArcGIS count does not match enumerated IDs")
        result = []
        ordered = sorted(ids)
        for offset in range(0, len(ordered), 100):
            requested = set(ordered[offset : offset + 100])
            data = self._query(
                layer,
                objectIds=",".join(map(str, sorted(requested))),
                outFields="*",
                returnGeometry="true",
                outSR=4326,
            )
            if data.get("exceededTransferLimit"):
                raise ValueError("ArcGIS batch truncated")
            if data.get("spatialReference", {}).get("wkid") != 4326:
                raise ValueError("ArcGIS did not return WGS84")
            rows = data.get("features", [])
            returned = [r["attributes"][oid_fields[0]] for r in rows]
            if len(returned) != len(requested) or set(returned) != requested:
                raise ValueError("ArcGIS batch IDs missing or duplicated")
            for row in rows:
                attrs, point = row["attributes"], row["geometry"]
                name = clean_name(attrs.get("tradename"))
                category = CATEGORY_RULES.get((layer, attrs.get("idfkod"), attrs.get("function")))
                result.append(
                    GeoFeature(
                        f"poi_{layer}",
                        str(attrs[oid_fields[0]]),
                        "poi",
                        name,
                        category if name else None,
                        validate_geometry(
                            {"type": "Point", "coordinates": [point["x"], point["y"]]}, "poi"
                        ),
                        attrs,
                    )
                )
        if self._ids(layer) != ids:
            raise ValueError("ArcGIS membership changed during extraction; retry the run")
        return result

    def _surfaces(self) -> list[GeoFeature]:
        package = self.downloader.json(STREETS_PACKAGE)
        if not package.get("success"):
            raise ValueError("CKAN lookup failed")
        data = package["result"]
        if (
            data.get("organization", {}).get("name") != "gis-sofia-eood"
            or data.get("license_id") != "cc-by"
        ):
            raise ValueError("Street provider or licence changed")
        resources = [
            r for r in data["resources"] if r["id"] == "47ec6ce6-4f51-4a7d-881d-007d9b009b8b"
        ]
        if len(resources) != 1:
            raise ValueError("Expected street resource is missing")
        raw = self.downloader.read(resources[0]["url"])
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if (
                len(members) != 1
                or members[0].filename != "streets_curbs.geojson"
                or members[0].file_size > 250_000_000
            ):
                raise ValueError("Unexpected or oversized street archive")
            with archive.open(members[0]) as member:
                content = member.read(250_000_001)
            if len(content) > 250_000_000:
                raise ValueError("Street data exceeds size limit")
        return geojson_features(json.loads(content), "street_surfaces", "surface")
