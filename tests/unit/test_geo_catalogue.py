"""Reference extraction completeness and coordinate validation regression tests."""

import io
import json
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from apartment_finder.application.use_cases.sync_geo_catalogue import sync_geo_catalogue
from apartment_finder.domain.geo import validate_geometry
from apartment_finder.infrastructure.geo.download import SnapshotDownloader, check_url
from apartment_finder.infrastructure.geo.sources import (
    SofiaPlanCatalogueSource,
    geojson_features,
)


def polygon(x=23.3):
    return {
        "type": "Polygon",
        "coordinates": [[[x, 42.6], [x + 0.01, 42.6], [x + 0.01, 42.61], [x, 42.6]]],
    }


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "Point", "coordinates": [322581, 4722265]},
        {"type": "Point", "coordinates": [42.7, 23.3]},
        {"type": "Point", "coordinates": [True, 42.7]},
        {"type": "Point", "coordinates": [float("nan"), 42.7]},
        {"type": "Point", "coordinates": []},
    ],
)
def test_invalid_coordinates(geometry):
    with pytest.raises(ValueError):
        validate_geometry(geometry, "poi")


def test_polygon_promotion_and_ring_validation():
    assert validate_geometry(polygon(), "boundary")["type"] == "MultiPolygon"
    invalid = polygon()
    invalid["coordinates"][0].pop()
    with pytest.raises(ValueError, match="closed"):
        validate_geometry(invalid, "boundary")


def test_duplicate_boundary_ids_rejected():
    feature = {"properties": {"id": "1", "name": "Test"}, "geometry": polygon()}
    with pytest.raises(ValueError, match="duplicate"):
        geojson_features(
            {"type": "FeatureCollection", "features": [feature, feature]},
            "planning_units",
            "boundary",
        )


class ArcgisFake:
    def __init__(self, size=2101, mode=None):
        self.size, self.mode = size, mode
        self.enumerations = 0
        self.batches = 0

    def json(self, url):
        query = parse_qs(urlsplit(url).query)
        if not urlsplit(url).path.endswith("query"):
            return {
                "geometryType": "esriGeometryPoint",
                "fields": [
                    {"name": "objectid", "type": "esriFieldTypeOID"},
                    *({"name": name} for name in ("idfkod", "function", "tradename")),
                ],
            }
        if "returnIdsOnly" in query:
            self.enumerations += 1
            size = self.size + (self.mode == "drift" and self.enumerations > 1)
            return {"objectIds": list(range(size))}
        if "returnCountOnly" in query:
            return {"count": self.size}
        self.batches += 1
        ids = list(map(int, query["objectIds"][0].split(",")))
        if self.mode == "missing":
            ids.pop()
        if self.mode == "duplicate":
            ids[-1] = ids[0]
        return {
            "spatialReference": {"wkid": 7801 if self.mode == "crs" else 4326},
            "exceededTransferLimit": self.mode == "limited",
            "features": [
                {
                    "attributes": {
                        "objectid": i,
                        "idfkod": "5.1.3",
                        "function": "Болници",
                        "tradename": "Test hospital",
                    },
                    "geometry": {"x": 23.3, "y": 42.7},
                }
                for i in ids
            ],
        }


def test_arcgis_fetches_more_than_record_limit():
    fake = ArcgisFake()
    features = SofiaPlanCatalogueSource(fake)._pois(13)
    assert len(features) == 2101
    assert fake.batches == 22
    assert all(feature.category == "hospital" for feature in features)


@pytest.mark.parametrize("mode", ["missing", "duplicate", "drift", "limited", "crs"])
def test_arcgis_incomplete_or_drifting_snapshot_fails(mode):
    with pytest.raises(ValueError):
        SofiaPlanCatalogueSource(ArcgisFake(mode=mode))._pois(13)


def test_category_requires_matching_code_and_label():
    features = SofiaPlanCatalogueSource(ArcgisFake(size=1))._pois(19)
    assert features[0].category is None


@pytest.mark.parametrize(
    "url",
    [
        "http://ge.sofiaplan.bg/",
        "https://evil.test/",
        "https://ge.sofiaplan.bg:444/",
        "https://u@ge.sofiaplan.bg/",
    ],
)
def test_download_host_allowlist(url):
    with pytest.raises(ValueError):
        check_url(url)


def test_snapshot_hash_and_arcgis_error(tmp_path):
    class Response(io.BytesIO):
        pass

    class Opener:
        def open(self, *args, **kwargs):
            return Response(b'{"error":{"message":"failure"}}')

    downloader = SnapshotDownloader(tmp_path)
    downloader.opener = Opener()
    with pytest.raises(ValueError):
        downloader.json("https://ge.sofiaplan.bg/")
    assert len(downloader.records) == 1
    assert len(list(tmp_path.iterdir())) == 1


def test_street_export_validation():
    sample = json.loads(Path("docs/samples/gis-sofia/streets_curbs.sample.geojson").read_text())
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("streets_curbs.geojson", json.dumps(sample))

    class Downloader:
        def json(self, url):
            return {
                "success": True,
                "result": {
                    "organization": {"name": "gis-sofia-eood"},
                    "license_id": "cc-by",
                    "resources": [{"id": "47ec6ce6-4f51-4a7d-881d-007d9b009b8b", "url": "unused"}],
                },
            }

        def read(self, url):
            return output.getvalue()

    features = SofiaPlanCatalogueSource(Downloader())._surfaces()
    assert len(features) == len(sample["features"])
    assert all(feature.geometry["type"] == "MultiPolygon" for feature in features)


def test_sync_failure_does_not_publish():
    class Source:
        def fetch(self):
            raise ValueError("Incomplete source")

    class Repository:
        def publish(self, *args):
            pytest.fail("Must not publish after a failed extraction")

    with pytest.raises(ValueError):
        sync_geo_catalogue(Source(), Repository())


def test_replay_uses_verified_snapshot_without_network(tmp_path, monkeypatch):
    from apartment_finder.infrastructure.geo.download import SnapshotReplayDownloader

    class Opener:
        def open(self, *args, **kwargs):
            return io.BytesIO(b'{"type":"FeatureCollection","features":[]}')

    url = "https://ge.sofiaplan.bg/"
    downloader = SnapshotDownloader(tmp_path)
    downloader.opener = Opener()
    expected = downloader.json(url)

    def forbidden(*args, **kwargs):
        pytest.fail("Replay must not make network requests")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", forbidden)
    replay = SnapshotReplayDownloader({"snapshots": downloader.records})
    assert replay.json(url) == expected
    next(tmp_path.iterdir()).write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="checksum"):
        replay.json(url)
