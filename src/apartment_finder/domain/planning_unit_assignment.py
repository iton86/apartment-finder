"""Validated handoff from reviewed or calibrated planning-unit resolution."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlanningUnitAssignment(BaseModel):
    """One accepted unit assignment; never an apartment coordinate.

    Calibrated confidence refers to the selected planning unit, not pin accuracy.
    Human review is a separate acceptance path with no probability claim.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)

    listing_id: UUID
    planning_unit_id: str = Field(min_length=1, max_length=100)
    release_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    description_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolver_version: str = Field(min_length=1, max_length=200)
    basis: Literal["calibrated", "human_reviewed"]
    calibrator_version: str | None = Field(default=None, min_length=1, max_length=200)
    confidence: float | None = Field(default=None, ge=0, le=1)
    runner_up_confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: list[str] = Field(min_length=1, max_length=30)
    reviewed_by: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def check_acceptance(self):
        """Reject ambiguous machine output and untraceable review claims."""
        if any(not text.strip() or len(text) > 2000 for text in self.evidence):
            raise ValueError("Evidence must contain nonblank, bounded descriptions")
        if not self.planning_unit_id.strip() or not self.resolver_version.strip():
            raise ValueError("Unit and resolver version cannot be blank")
        if self.basis == "calibrated":
            if not self.calibrator_version or not self.calibrator_version.strip():
                raise ValueError("Calibrated assignments require calibrator provenance")
            if self.confidence is None or self.runner_up_confidence is None:
                raise ValueError("Calibrated assignments require top and runner-up probabilities")
            if self.confidence + self.runner_up_confidence > 1.000001:
                raise ValueError("Top and runner-up probabilities cannot sum above one")
            if self.confidence < 0.9 or self.confidence - self.runner_up_confidence < 0.15:
                raise ValueError("Assignment does not meet confidence and ambiguity gates")
        else:
            if not self.reviewed_by or not self.reviewed_by.strip():
                raise ValueError("Human review requires reviewer provenance")
            if self.confidence is not None or self.runner_up_confidence is not None:
                raise ValueError("Human review does not establish a probability")
        return self
