"""
Step 2 of the POI pipeline: dump listing descriptions to JSONL for labeling.

Reads every listing with a description (active AND expired — the text is
still a valid training example after the ad goes offline), cleans it,
collapses exact and near duplicates, and writes one row per distinct ad.

The read happens in a READ ONLY transaction, so even with APP_ENV=cloud
this script cannot modify the database.

Run with:
    uv run python -m training.export_descriptions
    uv run python -m training.export_descriptions --out data/raw.jsonl --min-chars 80
"""

import argparse
import json
import logging
import re
from collections import Counter
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from apartment_finder.infrastructure.persistence.models import ListingModel
from apartment_finder.infrastructure.persistence.settings import build_postgres_connection_url
from apartment_finder.interface.logging_config import configure_logging
from training.dedup import cluster_near_duplicates, content_hash
from training.text_cleaning import clean_description

logger = logging.getLogger(__name__)

# Cheap signal for whether an ad mentions anything nearby. Not a label —
# only used to report the mix and, in Step 3, to make sure enough
# "no POI" ads end up in the labeled set so the model learns to say [].
_POI_HINT = re.compile(
    r"метро|спирк|трамва|автобус|училищ|гимназ|детск[аи] градин|университет|парк|"
    r"\bмол\b|магазин|супермаркет|лидл|билла|кауфланд|фантастико|болниц|аптек|"
    r"фитнес|център|\d+\s*(?:мин|м\.?\b|км)|в близост|близо до|до\s+(?:бул|ул)\.",
    re.IGNORECASE,
)


def fetch_listings(connection_url: str) -> list[ListingModel]:
    engine = create_engine(connection_url, pool_pre_ping=True)
    with Session(engine) as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        # Newest first: when duplicates collapse, the first one seen is kept,
        # so the surviving row points at the most recent URL and status.
        stmt = (
            select(ListingModel)
            .where(ListingModel.description.is_not(None), ListingModel.description != "")
            .order_by(ListingModel.last_seen_at.desc())
        )
        listings = list(session.scalars(stmt))
        session.expunge_all()
    engine.dispose()
    return listings


def build_rows(listings: list[ListingModel], min_chars: int, threshold: float) -> tuple[list, dict]:
    stats: Counter[str] = Counter(listings_read=len(listings))

    # Pass 1 — clean and drop exact duplicates (same text modulo whitespace,
    # case and punctuation).
    by_hash: dict[str, dict] = {}
    for listing in listings:
        description = clean_description(listing.description)
        if len(description) < min_chars:
            stats["dropped_too_short"] += 1
            continue
        digest = content_hash(description)
        if digest in by_hash:
            by_hash[digest]["duplicate_listing_ids"].append(
                f"{listing.source}:{listing.external_id}"
            )
            stats["dropped_exact_duplicate"] += 1
            continue
        by_hash[digest] = {
            "id": f"{listing.source}:{listing.external_id}",
            "source": listing.source,
            "external_id": listing.external_id,
            "url": listing.url,
            "title": listing.title,
            "city": listing.city,
            "area": listing.area,
            "street": listing.street,
            "transaction_type": listing.transaction_type.value,
            "status": listing.status.value,
            "last_seen_at": listing.last_seen_at.isoformat(),
            "description": description,
            "char_count": len(description),
            "content_hash": digest,
            "duplicate_listing_ids": [],
        }

    # Pass 2 — near duplicates. One representative per cluster goes into
    # the output; the others are recorded on it rather than silently lost.
    candidates = list(by_hash.values())
    cluster_ids = cluster_near_duplicates([r["description"] for r in candidates], threshold)
    rows: dict[int, dict] = {}
    for row, cluster_id in zip(candidates, cluster_ids, strict=True):
        if cluster_id in rows:
            rows[cluster_id]["duplicate_listing_ids"].extend(
                [row["id"], *row["duplicate_listing_ids"]]
            )
            stats["dropped_near_duplicate"] += 1
            continue
        row["cluster_id"] = cluster_id
        row["poi_hint"] = bool(_POI_HINT.search(row["description"]))
        rows[cluster_id] = row

    output = list(rows.values())
    stats["rows_written"] = len(output)
    stats["rows_with_poi_hint"] = sum(r["poi_hint"] for r in output)
    lengths = sorted(r["char_count"] for r in output)
    report = {
        **stats,
        "near_duplicate_threshold": threshold,
        "char_count_p50": lengths[len(lengths) // 2] if lengths else 0,
        "char_count_p95": lengths[int(len(lengths) * 0.95)] if lengths else 0,
        "char_count_max": lengths[-1] if lengths else 0,
        "by_status": Counter(r["status"] for r in output),
        "by_transaction_type": Counter(r["transaction_type"] for r in output),
        "top_areas": Counter(f"{r['city']} / {r['area']}" for r in output).most_common(15),
    }
    return output, report


def main() -> None:
    parser = argparse.ArgumentParser(description="Export listing descriptions for POI labeling")
    parser.add_argument("--out", type=Path, default=Path("data/raw.jsonl"))
    parser.add_argument(
        "--min-chars",
        type=int,
        default=80,
        help="Drop descriptions shorter than this after cleaning (default: 80)",
    )
    parser.add_argument(
        "--near-duplicate-threshold",
        type=float,
        default=0.8,
        help="Word-trigram Jaccard at or above which two ads count as one (default: 0.8)",
    )
    parser.add_argument(
        "--log-level", default=None, choices=["debug", "info", "warning", "error", "critical"]
    )
    args = parser.parse_args()
    configure_logging(args.log_level)

    listings = fetch_listings(build_postgres_connection_url())
    logger.info("Read %d listings with a description", len(listings))
    rows, report = build_rows(listings, args.min_chars, args.near_duplicate_threshold)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    report_path = args.out.with_name(args.out.stem + "_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("Wrote %d rows to %s (report: %s)", len(rows), args.out, report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
