"""Resolve listings, review candidate units and train held-out location calibration."""

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import create_engine

from apartment_finder.application.use_cases.resolve_listing_locations import (
    resolve_listing_locations,
    resolver_context,
)
from apartment_finder.domain.location_model import model_ready, train_location_model
from apartment_finder.infrastructure.geo.download import SnapshotDownloader
from apartment_finder.infrastructure.geo.reference_source import fetch_resolution_references
from apartment_finder.infrastructure.geo.resolution_repository import PostgisResolutionRepository
from apartment_finder.infrastructure.persistence.settings import build_postgres_connection_url


def read_json(path):
    """Load a bounded local configuration/model file."""
    if path is None:
        return None
    if path.stat().st_size > 10_000_000:
        raise ValueError("Configuration exceeds 10 MB")
    return json.loads(path.read_text())


def main():
    """Run local reference import, ranking, review, reports or supervised calibration."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    references = sub.add_parser("references", help="Import official addresses and street aliases")
    references.add_argument("--snapshot-dir", type=Path, default=Path("data/geo/references"))
    resolve = sub.add_parser(
        "resolve", help="Rank new/changed ads; abstain without validated model"
    )
    resolve.add_argument("--limit", type=int, default=100)
    resolve.add_argument("--model", type=Path)
    resolve.add_argument("--crosswalk", type=Path)
    report = sub.add_parser("report", help="Export recent candidate reports for human review")
    report.add_argument("--limit", type=int, default=100)
    review = sub.add_parser("review", help="Record one reviewed unit or explicit unknown label")
    review.add_argument("resolution_id")
    selected = review.add_mutually_exclusive_group(required=True)
    selected.add_argument("--unit")
    selected.add_argument("--unknown", action="store_true")
    review.add_argument("--reviewer", required=True)
    review.add_argument(
        "--group", required=True, help="Stable physical-property/duplicate-ad group"
    )
    review.add_argument("--geography-group", required=True, help="Spatial holdout group")
    review.add_argument("--split", choices=["train", "calibration", "test"], required=True)
    train = sub.add_parser(
        "train", help="Train on reviewed property groups and evaluate held-out data"
    )
    train.add_argument("--output", type=Path, required=True)
    train.add_argument("--crosswalk", type=Path)
    coordinate = sub.add_parser(
        "coordinate", help="Record an independently sourced listing coordinate"
    )
    coordinate.add_argument("listing_id")
    coordinate.add_argument("--longitude", type=float, required=True)
    coordinate.add_argument("--latitude", type=float, required=True)
    coordinate.add_argument("--uncertainty-m", type=float, required=True)
    coordinate.add_argument("--source", required=True)
    args = parser.parse_args()
    if os.environ.get("APP_ENV", "local") != "local":
        parser.error("Location commands currently support APP_ENV=local only")
    if hasattr(args, "limit") and not 1 <= args.limit <= 10000:
        parser.error("limit must be between 1 and 10,000")
    engine = create_engine(build_postgres_connection_url(), pool_pre_ping=True)
    try:
        repository = PostgisResolutionRepository(engine)
        if args.command == "references":
            downloader = SnapshotDownloader(args.snapshot_dir)
            bundle = fetch_resolution_references(downloader)
            identifier = repository.publish_references(bundle)
            manifest = args.snapshot_dir / ("manifest-" + identifier + ".json")
            manifest.write_text(json.dumps(bundle["manifest"], indent=2))
            result = {
                "bundle_id": identifier,
                "addresses": len(bundle["addresses"]),
                "street_aliases": len(bundle["aliases"]),
                "manifest_path": str(manifest),
            }
        elif args.command == "coordinate":
            result = {
                "observation_id": repository.record_coordinate(
                    args.listing_id, args.longitude, args.latitude, args.uncertainty_m, args.source
                )
            }
        elif args.command == "resolve":
            result = resolve_listing_locations(
                repository, args.limit, read_json(args.model), read_json(args.crosswalk)
            )
        elif args.command == "review":
            result = {
                "review_id": repository.review(
                    args.resolution_id,
                    args.unit,
                    args.reviewer,
                    args.group,
                    args.split,
                    args.geography_group,
                )
            }
        elif args.command == "report":
            result = repository.results(args.limit)
        else:
            context = resolver_context(repository, read_json(args.crosswalk) or {})
            model = train_location_model(repository.reviewed_examples(context), context)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.output.with_suffix(args.output.suffix + ".tmp")
            temporary.write_text(json.dumps(model, indent=2, allow_nan=False))
            temporary.replace(args.output)
            result = {
                "model_path": str(args.output),
                "version": model["version"],
                "test_metrics": model["test_metrics"],
                "acceptance_enabled": model_ready(model),
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except ValueError as error:
        parser.error(str(error))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
