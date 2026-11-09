"""Rank planning units, abstain without calibrated evidence, and persist provenance."""

import json

from apartment_finder.application.ports import LocationResolutionRepository
from apartment_finder.domain.geo import fingerprint
from apartment_finder.domain.location_model import model_ready, probabilities, validate_model
from apartment_finder.domain.location_resolution import (
    RESOLVER_VERSION,
    Evidence,
    Place,
    extract_evidence,
    normalize_name,
    rank_units,
)
from apartment_finder.domain.planning_unit_assignment import PlanningUnitAssignment


def resolver_context(repository: LocationResolutionRepository, crosswalk: dict) -> dict:
    """Pin every externally changeable resolver input into the model context."""
    return {
        "release_id": repository.release_id,
        "bundle_id": repository.bundle_id,
        "resolver_version": RESOLVER_VERSION,
        "crosswalk_hash": fingerprint(crosswalk),
    }


def resolve_listing_locations(
    repository: LocationResolutionRepository,
    limit: int = 100,
    model: dict | None = None,
    crosswalk: dict | None = None,
) -> dict:
    """Resolve new/changed listings; uncalibrated candidates remain reviewable, not accepted."""
    if not 1 <= limit <= 10000:
        raise ValueError("Resolution limit must be between 1 and 10,000")
    crosswalk = crosswalk or {}
    if crosswalk and (
        not isinstance(crosswalk.get("reviewed_by"), str) or not crosswalk["reviewed_by"].strip()
    ):
        raise ValueError("Neighbourhood crosswalk requires review provenance")
    if crosswalk:
        mapping = crosswalk.get("units")
        if not isinstance(mapping, dict):
            raise ValueError("Crosswalk units must map neighbourhood names to unit-ID lists")
        known = repository.known_units()
        for area, units in mapping.items():
            if (
                not isinstance(area, str)
                or area != normalize_name(area)
                or not isinstance(units, list)
                or not units
                or len(units) > 564
                or any(not isinstance(unit, str) or unit not in known for unit in units)
                or len(units) != len(set(units))
            ):
                raise ValueError("Crosswalk needs normalized names and unique current unit IDs")
    context = resolver_context(repository, crosswalk)
    if model is not None:
        validate_model(model, context)
    policy_id = repository.activate_policy(
        context | {"model_version": model["version"] if model else None}
    )
    index = repository.index()
    counts = {"saved": 0, "unchanged": 0, "stale": 0, "accepted": 0, "ambiguous": 0, "unmatched": 0}
    for listing in repository.listings():
        input_hash = fingerprint(
            {
                "listing": listing.snapshot_hash,
                "context": context,
                "model": model["version"] if model else None,
            }
        )
        if repository.exists(listing.listing_id, input_hash):
            counts["unchanged"] += 1
            continue
        evidence, notes = extract_evidence(listing, index)
        supports = [
            repository.support(
                tuple(sorted((p.dataset, p.external_id) for p in g.places)), g.radius_m
            )
            for g in evidence
        ]
        units = crosswalk.get("units", {}).get(normalize_name(listing.area), [])
        if units:
            evidence.append(
                Evidence(
                    "neighbourhood:" + normalize_name(listing.area),
                    "neighbourhood",
                    "Reviewed neighbourhood crosswalk: " + listing.area,
                    [Place("planning_units", u, u, "neighbourhood") for u in units],
                    1.0,
                    0.0,
                    0.3,
                )
            )
            supports.append({unit: 1 / len(units) for unit in units})
        result = rank_units(evidence, supports, notes)
        result.update(
            {
                "id": fingerprint([listing.listing_id, input_hash]),
                "listing_id": listing.listing_id,
                "input_fingerprint": input_hash,
                "listing_snapshot_hash": listing.snapshot_hash,
                "observations": {
                    "description": listing.description,
                    "city": listing.city,
                    "area": listing.area,
                    "street": listing.street,
                },
                "prediction_fingerprint": listing.prediction_hash,
                "description_sha256": listing.text_hash,
                "poi_model_version": listing.poi_model_version,
                "context": context,
                "policy_id": policy_id,
                "observed_at": listing.observed_at,
                "resolver_version": RESOLVER_VERSION,
                "status": "ambiguous" if result["candidates"] else "unmatched",
                "assignment": None,
            }
        )
        if model is not None:
            ranked = probabilities(result["candidates"], model)
            result["probabilities"] = [
                {"planning_unit_id": unit, "probability": p} for unit, p in ranked
            ]
            top, probability = ranked[0]
            runner = ranked[1][1] if len(ranked) > 1 else 0.0
            result["model_version"] = model["version"]
            if (
                model_ready(model)
                and top is not None
                and probability >= 0.9
                and probability - runner >= 0.15
                and not result["conflict"]
            ):
                candidate = next(c for c in result["candidates"] if c["planning_unit_id"] == top)
                assignment = PlanningUnitAssignment.model_validate_json(
                    json.dumps(
                        {
                            "listing_id": listing.listing_id,
                            "planning_unit_id": top,
                            "release_id": repository.release_id,
                            "description_sha256": listing.text_hash,
                            "resolver_version": RESOLVER_VERSION,
                            "basis": "calibrated",
                            "calibrator_version": model["version"],
                            "confidence": probability,
                            "runner_up_confidence": runner,
                            "evidence": candidate["evidence"] or ["Spatial model support"],
                        }
                    )
                )
                result["assignment"] = assignment.model_dump(mode="json")
                result["status"] = "accepted"
        if repository.save(listing, result):
            counts["saved"] += 1
            counts[result["status"]] += 1
        else:
            counts["stale"] += 1
        if counts["saved"] + counts["stale"] >= limit:
            break
    return {
        "counts": counts,
        "context": context,
        "calibrated_acceptance_enabled": model is not None and model_ready(model),
    }
