# GIS-Sofia official street and address exports

Verified 2026-11-08. This discovery supersedes the polygon availability gap in
[the earlier iSofMap investigation](isofmap-street-discovery.md).

GIS-Sofia's [official announcement](https://www.gis-sofia.bg/bg/post/gis-sofiya-eood-veche-e-chast-ot-initsiativata-na-stolichna-obshtina-za-izgrajdane-na-edinen-portal-za-gradski-danni)
links its published datasets to Urban Data Sofia. The CKAN catalogue provides
HTTPS file downloads with explicit licensing metadata. Prefer these exports
for the initial reference-data importer.

## Available datasets

| Dataset | Format | Verification |
|---|---|---|
| [Улици и тротоари](https://urbandata.sofia.bg/dataset/streets_curbs-zip) | ZIP containing GeoJSON | Entire archive downloaded and parsed: 91,244 features; 91,242 Polygon and 2 MultiPolygon |
| [Стари/нови имена на улици](https://urbandata.sofia.bg/dataset/streets_names_new_old-zip) | ZIP containing semicolon-delimited CSV | Entire archive downloaded and parsed; fields `rn`, `new_name`, `name`, `resolution_renaming` |
| [Адреси на територията на Столична община](https://urbandata.sofia.bg/dataset/address_sofia-zip) | GeoJSON and zipped CSV | Resource URLs and metadata verified through CKAN; payload not downloaded in this investigation |

All three catalogue records declare `license_id=cc-by` (Creative Commons
Attribution) and data current as of 28 August 2026. Preserve exact licence
metadata and attribution with each snapshot; do not transfer this licence
assumption to other GIS-Sofia or iSofMap datasets. Metadata/resource modification
timestamps differ from the stated date of the underlying data.

## Verified street polygon schema

Properties: `rn`, `type`, `street_name`, `region`, `area`. The inspected export
contains longitude/latitude coordinates around Sofia and no explicit `crs`
member; validate as WGS84 GeoJSON before importing. Example street:
`ул. Проф. Тошко Петров`, district `Люлин`.

These are individual surface pieces, not one polygon per named street. The
`type` field includes 39,100 ordinary sidewalk features, 33,348 ordinary
carriageway features, and other surfaces such as unpaved carriageways, bridges,
parking, bicycle paths, greenery, medians and tram tracks. A street-road filter
must explicitly select reviewed surface types; do not classify all 91,244
features as roads. Store the original subtype and geometry.

The export's `rn` values are not verified as stable identifiers across releases.
Do not join `rn` to the separate name-history CSV. Match old/current names with
settlement/district context where possible and preserve ambiguity. Street-name
renaming can be one-to-many; an old name alone does not establish a unique match.

Representative [geometry samples](samples/gis-sofia/streets_curbs.sample.geojson),
[name-history samples](samples/gis-sofia/street_names.sample.json), and a
[discovery manifest](samples/gis-sofia/discovery.json) are saved in this repository.
The manifest includes full catalogue metadata, source URL, archive hash, counts
and subtype inventory. Complete archives were inspected in `/tmp`; they are not
committed project assets or persistent production snapshots.

## Import and update design

Use the existing proposed snapshot/validate/publish flow with a CKAN export
adapter. Discover resources through:

```text
https://urbandata.sofia.bg/api/3/action/package_show?id=streets_curbs-zip
https://urbandata.sofia.bg/api/3/action/package_show?id=streets_names_new_old-zip
https://urbandata.sofia.bg/api/3/action/package_show?id=address_sofia-zip
```

Pin reviewed package/resource IDs, verify provider and format, and allowlist
resource download hosts. Poll metadata weekly and support manual refresh.
Download changed resources, calculate SHA-256, preserve the raw ZIP and manifest,
and publish only a fully validated release. Treat metadata changes as hints;
periodically verify content even if timestamps are unchanged. Replay raw files
when mapping rules change. Validate compressed and uncompressed size limits,
archive member names, geometry, coordinate bounds, required fields, counts and
subtypes. The street archive is approximately 26.7 MB compressed and 102 MB
uncompressed; inspect ZIP metadata before allocating or extracting.

Populate `geo.road_area_version` with `geometry(MultiPolygon,4326)`, original
surface type, street name, district, source row key, attributes, content hash,
and release identity. Add a GiST geometry index and geography expression index
for metric proximity. Normalize Polygon to MultiPolygon. Recompute area in an
appropriate metric CRS when needed; do not assume the string `area` field has
verified units or precision.

Until ID stability is established, replace the complete versioned surface
snapshot on each release and re-resolve affected references. Do not carry a
match forward solely because `rn` was reused. Maintain reviewed street entities
separately from their many surface parts; aggregation by name alone can merge
different streets. Do not automatically union sidewalks into the carriageway.
These polygons establish surface proximity, not network routing or precise
building entrance access.

This is source discovery and an architecture update. A production importer,
PostGIS migration and refresh schedule remain to be implemented.
