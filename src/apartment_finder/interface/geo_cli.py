"""Manually download reference catalogues and locate points in planning units."""

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import create_engine

from apartment_finder.application.use_cases.sync_geo_catalogue import sync_geo_catalogue
from apartment_finder.infrastructure.geo.download import (
    SnapshotDownloader,
    SnapshotReplayDownloader,
)
from apartment_finder.infrastructure.geo.repository import PostgisCatalogueRepository
from apartment_finder.infrastructure.geo.sources import SofiaPlanCatalogueSource
from apartment_finder.infrastructure.persistence.settings import build_postgres_connection_url


def main():
    """Run a dry import, publish a complete release, or look up a WGS84 point."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync", help="Download all sources; dry-run unless --publish is set")
    sync.add_argument("--snapshot-dir", type=Path, default=Path("data/geo/snapshots"))
    sync.add_argument("--publish", action="store_true")
    sync.add_argument(
        "--repair-invalid",
        action="store_true",
        help="Opt in to audited PostGIS polygon repairs during publication",
    )
    sync.add_argument("--replay-manifest", type=Path)
    sync.add_argument("--expected-boundaries", type=int, default=564)
    locate = sub.add_parser("locate", help="Find every planning unit covering a point")
    locate.add_argument("--longitude", type=float, required=True)
    locate.add_argument("--latitude", type=float, required=True)
    map_command = sub.add_parser("map", help="Serve the local planning-unit explorer")
    map_command.add_argument("--port", type=int, default=8765)
    map_command.add_argument(
        "--assignments",
        type=Path,
        help="JSON handoff of calibrated or human-reviewed unit assignments",
    )
    map_command.add_argument(
        "--preview-listing-port",
        type=int,
        help="Show unreviewed candidate previews from this local listing DB port",
    )
    args = parser.parse_args()
    if args.command == "map" and args.preview_listing_port is not None:
        if args.assignments or not 1 <= args.preview_listing_port <= 65535:
            parser.error("Preview needs a valid listing port and cannot use --assignments")
    downloader = None
    if args.command == "sync":
        if args.repair_invalid and not args.publish:
            parser.error("--repair-invalid requires --publish (PostGIS performs the repair)")
        downloader = (
            SnapshotReplayDownloader(json.loads(args.replay_manifest.read_text()))
            if args.replay_manifest
            else SnapshotDownloader(args.snapshot_dir)
        )
    if args.command == "sync" and not args.publish:
        report = sync_geo_catalogue(SofiaPlanCatalogueSource(downloader, args.expected_boundaries))
    else:
        if os.environ.get("APP_ENV", "local") != "local":
            parser.error("Geospatial database commands currently support APP_ENV=local only")
        engine = create_engine(build_postgres_connection_url(), pool_pre_ping=True)
        try:
            repository = PostgisCatalogueRepository(
                engine, repair_invalid=getattr(args, "repair_invalid", False)
            )
            if args.command == "sync":
                report = sync_geo_catalogue(
                    SofiaPlanCatalogueSource(downloader, args.expected_boundaries),
                    repository,
                )
            elif args.command == "map":
                from apartment_finder.infrastructure.geo.map_repository import (
                    PostgisPlanningUnitMapRepository,
                )
                from apartment_finder.interface.map_server import serve_map

                if args.preview_listing_port is not None:
                    from sqlalchemy.engine import make_url

                    from apartment_finder.infrastructure.geo.candidate_preview import (
                        CandidatePreviewRepository,
                    )

                    url = make_url(build_postgres_connection_url())
                    if url.host not in {"localhost", "127.0.0.1", "::1"}:
                        parser.error("Candidate preview requires a local database")
                    listing_engine = create_engine(url.set(port=args.preview_listing_port))
                    try:
                        serve_map(CandidatePreviewRepository(engine, listing_engine), args.port)
                    finally:
                        listing_engine.dispose()
                else:
                    serve_map(PostgisPlanningUnitMapRepository(engine), args.port, args.assignments)
                return
            else:
                report = {"planning_units": repository.locate(args.longitude, args.latitude)}
        finally:
            engine.dispose()
    if args.command == "sync":
        from apartment_finder.domain.geo import fingerprint

        args.snapshot_dir.mkdir(parents=True, exist_ok=True)
        path = args.snapshot_dir / ("manifest-" + fingerprint(report["manifest"]) + ".json")
        path.write_text(json.dumps(report["manifest"], ensure_ascii=False, indent=2))
        report["manifest_path"] = str(path)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
