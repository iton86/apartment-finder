"""Official address/alias completeness checks without network access."""

import io
import json
import zipfile

import pytest

from apartment_finder.infrastructure.geo.reference_source import fetch_resolution_references


class ReferenceDownloads:
    records = {}

    def __init__(self, broken=False):
        self.broken = broken

    def json(self, url):
        identifier = (
            "7b32c5f8-5f25-4b24-92f9-43e265275587"
            if "address_sofia" in url
            else "c3ec6293-b1b0-48b7-98ae-9693ae7abd77"
        )
        return {
            "success": True,
            "result": {
                "license_id": "cc-by",
                "organization": {"name": "gis-sofia-eood"},
                "resources": [
                    {"id": identifier, "url": "addresses" if "address_sofia" in url else "aliases"}
                ],
            },
        }

    def read(self, url):
        if url == "addresses":
            return json.dumps(
                {
                    "type": "FeatureCollection",
                    "numberMatched": 2 if self.broken else 1,
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {
                                "rn": "1",
                                "street": "ул. Нова",
                                "streetnum": "12",
                                "settlement": "гр. София",
                            },
                            "geometry": {"type": "Point", "coordinates": [23.3, 42.7]},
                        }
                    ],
                }
            ).encode()
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr(
                "streets_names_new_old.csv",
                "rn;new_name;name;resolution_renaming\n1;ул. Нова;ул. Стара;Official rename\n",
            )
        return output.getvalue()


def test_official_reference_exports_parse_and_preserve_aliases():
    bundle = fetch_resolution_references(ReferenceDownloads())
    assert len(bundle["addresses"]) == 1 and len(bundle["aliases"]) == 1
    assert bundle["addresses"][0]["name_key"] == "нова"


def test_incomplete_address_export_rejected():
    with pytest.raises(ValueError, match="incomplete"):
        fetch_resolution_references(ReferenceDownloads(broken=True))
