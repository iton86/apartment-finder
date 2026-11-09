"""Assemble a map without treating unit assignments as precise locations."""

from apartment_finder.application.ports import PlanningUnitMapRepository
from apartment_finder.domain.planning_unit_assignment import PlanningUnitAssignment


def planning_unit_map(
    repository: PlanningUnitMapRepository, assignments: list[PlanningUnitAssignment] | None
) -> dict:
    """Reject duplicate assignments before querying the read-only catalogue."""
    identities = [assignment.listing_id for assignment in (assignments or [])]
    if len(identities) != len(set(identities)):
        raise ValueError("Each listing can have only one accepted planning unit")
    return repository.map_data(assignments)
