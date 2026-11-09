# iSofMap street geometry discovery

Update: [GIS-Sofia official exports](gis-sofia-open-data.md) now provide verified
street/sidewalk polygons with names and a street-name history table. Prefer that
source for surface geometry; earlier iSofMap limitations below remain specific
to its tested WFS responses.

Inspected 2026-11-08. Public one-feature requests successfully retrieved street
lines and address coordinates. A complete street dataset and true road-surface
polygons have **not** been extracted or verified.

## Observed services and samples

The [application](http://www.isofmap.bg/) uses OpenLayers with a MapServer
endpoint. Its [map URL module](http://www.isofmap.bg/js/mapUrl.js) identifies
`/owsmap` for map and feature requests and `/owscap` for the layer catalogue.
The [selection module](http://www.isofmap.bg/js/selectFeature.js) makes WFS
GetFeature requests filtered by `adm_id`, requesting GeoJSON in EPSG:7801.

| Layer | Map label | Retrieved geometry | Sample properties |
|---|---|---|---|
| `addr_street` | Улици | MultiLineString | Empty |
| `addr_address` | Адресни точки | Point | `officialaddress` |
| `bm_street_all` | Улична мрежа - контур | LineString | Empty |
| `bm_street_fill` | Улична мрежа - запълване | LineString | Empty |
| `bm_street_main` | Главна улична мрежа | LineString | Empty |
| `street_reg_line` | Уличнорегулационни линии | LineString | Empty |
| `reg_axisline` | Осови линии | LineString | Empty |

The table describes one tested feature per layer, not an exhaustive geometry
inventory. `bm_street_all` and `bm_street_fill` returned identical sample
coordinates: map styling labels do not establish polygon geometry. Regulation
lines describe planning geometry; do not assume they represent current physical
road edges or a routable network. `streets_10000` appears in the WMS catalogue
but a WFS request returned an unknown-type error.

Five saved [sample responses and their manifest](samples/isofmap/manifest.json)
include request URLs, timestamps, and SHA-256 hashes. Files use `.native.json`
because they contain projected coordinates and should not be opened as ordinary
WGS84 GeoJSON. No login or private session was used.

A minimal tested request is:

```text
http://www.isofmap.bg/owsmap?service=WFS&version=1.1.0&request=GetFeature&typeName=ms:addr_street&maxFeatures=1&outputFormat=geojson&srsName=EPSG:7801
```

The first street sample starts at `[322581.5589, 4722265.845]`. These are projected
coordinates, not longitude/latitude. The address sample is a point with a full
`officialaddress` value. Street samples have neither names nor returned stable
feature IDs, which prevents reliable name matching or deduplication by ID yet.

## Coordinate and API limitations

The application's [projection definition](http://www.isofmap.bg/js/projectionImpl.js)
configures EPSG:7801. Separate test requests with `srsName=EPSG:4326` still
returned the same projected coordinates; the tested output ignored the requested
reprojection. Never assign SRID 4326 directly to these numbers. Confirm axis
order against known control locations, import in the verified source CRS, and
explicitly transform to 4326 using PostGIS or PROJ. Preserve native samples for
regression tests. Do not rely on an absent GeoJSON `crs` member.

WFS GetCapabilities and DescribeFeatureType returned XML exception reports saying
those requests are disabled, although GetFeature worked. Inspect response bodies
for OGC exceptions even with HTTP success. Standard capabilities-based schema,
pagination, count, and incremental-update discovery are therefore unverified.
Do not assume unrestricted GetFeature requests yield all features.

The supplied HTTP site works. HTTPS on `www.isofmap.bg` failed certificate
hostname validation during inspection. No certificate checks were disabled.
HTTP samples suffice for discovery, but production ingestion should use an
operator-confirmed HTTPS service or integrity-verifiable provider export.

## Recommended integration

Extend the [SofiaPlan catalogue architecture](sofiaplan-geospatial-architecture.md)
with a separate iSofMap source adapter and source manifest. Do not place streets
in its point-only `geo.poi_version` table.

- Add `geo.street_version`: release/feature key, provider ID when verified,
  source name and normalized name when available, settlement, street subtype,
  `geometry(MultiLineString,4326)`, native CRS, source attributes and content hash.
  Use a GiST geometry index and an expression GiST index on `geom::geography`
  for distance-to-street queries. Promote LineString to MultiLineString.
- Add `geo.address_version` if address geocoding is required: versioned source
  identity, original address, parsed fields with parsing status, and Point 4326.
  Treat it as address reference data, separate from model POI mentions.
- Keep any future verified road-area polygons in `geo.road_area_version` with
  MultiPolygon geometry and explicit current/planned/source semantics.
- Use `ST_DWithin(street.geom::geography, listing_point::geography, radius_m)`
  for proximity, after verifying the listing location independently. Preserve
  mention categories `street`, `boulevard`, and `ring_road`; geometry alone does
  not establish the subtype.

Before enabling full updates, establish feature enumeration, stable identity,
name lookup, completeness checks, and source update semantics. The public search
UI maps street results to `addr_street` and selects using `adm_id`; this suggests
a possible identity path, but its full enumeration and stability were not tested.
If only an official periodic export supplies those guarantees, ingest that export
through the same snapshot/validate/publish pipeline instead.

For actual polygons, request a road-surface dataset or street-regulation area
export from GIS-Sofia. Connecting regulation lines or buffering street lines
would produce derived approximations, not authoritative road polygons; label
and version such derived data separately if later needed.

The [published terms](http://www.isofmap.bg/terms) do not establish an open-data
licence for these samples. Confirm permitted bulk storage, refresh, and reuse
with the provider before production integration. No bulk importer, scheduled
requests, PostgreSQL migration, or full dataset is included in this discovery.
