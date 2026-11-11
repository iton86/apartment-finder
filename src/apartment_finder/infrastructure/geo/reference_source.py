"""Versioned official address points and street-name histories from Urban Data Sofia."""

import csv
import io
import json
import zipfile

from apartment_finder.domain.geo import fingerprint, validate_geometry
from apartment_finder.domain.location_resolution import normalize_name

PACKAGES = {
    "addresses": ("address_sofia-zip", "7b32c5f8-5f25-4b24-92f9-43e265275587"),
    "aliases": ("streets_names_new_old-zip", "c3ec6293-b1b0-48b7-98ae-9693ae7abd77"),
}


def fetch_resolution_references(downloader) -> dict:
    """Download and validate complete official exports with retained provenance."""
    payloads = {}
    for key, (package_id, resource_id) in PACKAGES.items():
        package = downloader.json(
            "https://urbandata.sofia.bg/api/3/action/package_show?id=" + package_id
        )
        data = package.get("result", {})
        if (
            not package.get("success")
            or data.get("organization", {}).get("name") != "gis-sofia-eood"
            or data.get("license_id") != "cc-by"
        ):
            raise ValueError("Reference provider or licence changed")
        resources = [r for r in data.get("resources", []) if r.get("id") == resource_id]
        if len(resources) != 1:
            raise ValueError("Reference resource missing")
        raw = downloader.read(resources[0]["url"])
        if key == "aliases":
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = archive.infolist()
                if (
                    len(members) != 1
                    or members[0].filename != "streets_names_new_old.csv"
                    or members[0].file_size > 10_000_000
                ):
                    raise ValueError("Unexpected alias archive")
                raw = archive.read(members[0])
        payloads[key] = raw
    data = json.loads(payloads["addresses"])
    if data.get("type") != "FeatureCollection" or data.get("crs"):
        raise ValueError("Expected official WGS84 address FeatureCollection")
    features = data.get("features", [])
    if not features or data.get("numberMatched") != len(features):
        raise ValueError("Address export incomplete")
    addresses, seen = [], set()
    for feature in features:
        attrs = feature["properties"]
        identifier = str(attrs["rn"])
        if identifier in seen:
            raise ValueError("Duplicate official address identity")
        seen.add(identifier)
        geometry = validate_geometry(feature["geometry"], "poi")
        addresses.append(
            {
                "external_id": identifier,
                "street": attrs["street"],
                "settlement": attrs["settlement"],
                "number": attrs["streetnum"],
                "name_key": normalize_name(attrs["street"]),
                "geometry": geometry,
                "attributes": attrs,
            }
        )
    aliases, seen = [], set()
    for row in csv.DictReader(io.StringIO(payloads["aliases"].decode("utf-8-sig")), delimiter=";"):
        if not {"rn", "new_name", "name", "resolution_renaming"}.issubset(row):
            raise ValueError("Alias export schema changed")
        if row["rn"] in seen:
            raise ValueError("Duplicate alias identity")
        seen.add(row["rn"])
        aliases.append(
            {
                "external_id": row["rn"],
                "old_name": row["name"],
                "new_name": row["new_name"],
                "source_note": row["resolution_renaming"],
            }
        )
    if not aliases:
        raise ValueError("Empty alias export")
    manifest = {"schema_version": 1, "snapshots": dict(downloader.records)}
    return {
        "id": fingerprint(manifest),
        "manifest": manifest,
        "addresses": addresses,
        "aliases": aliases,
    }
