"""Ports: the abstract contracts the application layer needs from scrapers, storage and services."""

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from apartment_finder.domain.entities import (
    DownloadedImage,
    Listing,
    ListingId,
    SearchListingsResult,
    SearchScope,
)
from apartment_finder.domain.geo import GeoFeature
from apartment_finder.domain.labeling import (
    LabelingBatch,
    LabelingTask,
    LabelStudioTaskPayload,
    LabelStudioTaskState,
    TeacherAnnotation,
)
from apartment_finder.domain.planning_unit_assignment import PlanningUnitAssignment


class ListingUnavailableError(Exception):
    """The page explicitly reports that the ad is unavailable."""


class SearchPageNotFoundError(Exception):
    """The search URL itself doesn't exist on the site (e.g. HTTP 404).

    A configuration problem rather than a transient one: retrying the same
    URL won't help, but it also says nothing about stored listings.
    """


@dataclass
class ScrapeOutcome:
    """One listing's result, success or failure.

    The failure is carried as data rather than raised because these arrive
    in a batch: on a 500-listing run, one ad with broken markup shouldn't
    abandon the 499 that would have worked.
    """

    url: str
    listing: Listing | None = None
    error: Exception | None = None


class SiteScraper(ABC):
    """Anything that can turn a search page into listing URLs, and a
    listing URL into a Listing, qualifies — regardless of whether it's
    built with Playwright, requests, or something else entirely."""

    source: str | None = None

    @abstractmethod
    def discover_listing_urls(self, search_url: str, max_pages: int) -> list[SearchListingsResult]:
        """Return the ads found on a search page, following up to ``max_pages`` pages.

        Raises:
            SearchPageNotFoundError: If the search URL itself does not exist.
        """
        ...

    def search_scope(self, search_url: str) -> SearchScope:
        """Describe the filters of a search URL; the default is unscoped (matches everything)."""
        return SearchScope()

    @abstractmethod
    def scrape_listing(self, url: str) -> Listing:
        """Fetch and parse one listing page.

        Raises:
            ListingUnavailableError: If the page reports that the ad is gone.
        """
        ...

    def scrape_listings(self, urls: Sequence[str]) -> Iterator[ScrapeOutcome]:
        """Scrape many listings, yielding each as it finishes.

        Concrete, not abstract, and deliberately: this sequential version is
        a correct implementation of the contract, so a test double or a
        simpler adapter gets batch semantics for free and only an adapter
        that can genuinely overlap requests needs to override it.

        Implementations may yield in any order — callers that need the
        original order must sort by themselves.
        """
        for url in urls:
            try:
                yield ScrapeOutcome(url=url, listing=self.scrape_listing(url))
            except Exception as exc:
                yield ScrapeOutcome(url=url, error=exc)


class ImageDownloader(ABC):
    """Fetches a listing's photos and stores them somewhere durable."""

    @abstractmethod
    def download(self, listing: Listing) -> list[DownloadedImage]:
        """Download and store every image of the listing, returning where each one landed."""
        ...


class ListingRepository(ABC):
    """Persistence contract — could be backed by Postgres, an in-memory
    dict for tests, or anything else."""

    @abstractmethod
    def save(self, listing: Listing) -> None:
        """Persist a new listing."""
        ...

    @abstractmethod
    def exists(self, listing_id) -> bool:
        """Return whether a listing with this ID is already stored."""
        ...

    @abstractmethod
    def filter_unseen(self, results: list[SearchListingsResult]) -> list[SearchListingsResult]:
        """Return only the search results whose listings are not stored yet."""
        ...

    @abstractmethod
    def all(self) -> list[Listing]:
        """Return every stored listing."""
        ...

    @abstractmethod
    def record_seen(self, listing_ids: set[ListingId], seen_at: datetime) -> None:
        """Refresh successful sightings and restore active status."""
        ...

    @abstractmethod
    def record_unreachable(self, listing_id: ListingId, checked_at: datetime) -> None:
        """Preserve last_seen_at; expire after more than three days unseen."""
        ...


class NotificationSender(ABC):
    """Tells the user about a listing through some channel (e.g. a chat message)."""

    @abstractmethod
    def notify(self, listing: Listing) -> None:
        """Send a notification for the listing."""
        ...


class LabelStudioGateway(ABC):
    """The slice of the Label Studio API the labeling workflow depends on."""

    @abstractmethod
    def import_tasks(
        self,
        project_id: int,
        tasks: list[LabelStudioTaskPayload],
    ) -> dict[str, int]:
        """Import tasks and return Label Studio task IDs keyed by task UUID string."""
        ...

    @abstractmethod
    def get_task(self, task_id: int) -> LabelStudioTaskState:
        """Fetch current task state, including saved annotations and predictions."""
        ...

    @abstractmethod
    def create_annotation(
        self,
        task_id: int,
        *,
        result: list[dict],
        completed_by: int | None = None,
    ) -> dict:
        """Create a saved annotation through a supported Label Studio API."""
        ...


class LabelingRepository(ABC):
    """Persistence contract for labeling tasks, export batches and annotation state."""

    @abstractmethod
    def reserve_candidates(self, limit: int) -> list[LabelingTask]:
        """Atomically reserve eligible listings for future annotation."""
        ...

    @abstractmethod
    def get_pending(self, limit: int) -> list[LabelingTask]:
        """Return pending tasks in creation order."""
        ...

    @abstractmethod
    def create_export_batch(
        self,
        *,
        project_id: int,
        limit: int,
        batch_id: UUID | None = None,
    ) -> LabelingBatch:
        """Claim pending tasks for one Label Studio export batch."""
        ...

    @abstractmethod
    def get_batch_tasks(self, batch_id: UUID) -> list[LabelingTask]:
        """Return tasks in a selection/export batch."""
        ...

    @abstractmethod
    def get_by_label_studio_task_id(self, label_studio_task_id: int) -> LabelingTask | None:
        """Find the selected snapshot exported as a Label Studio task."""
        ...

    @abstractmethod
    def save_teacher_annotation(self, task_id: UUID, annotation: TeacherAnnotation) -> None:
        """Store the teacher model's annotation (or its failure status) on the task."""
        ...

    @abstractmethod
    def mark_exported(self, task_id: UUID, *, project_id: int, label_studio_task_id: int) -> None:
        """Record the Label Studio task created for a selected snapshot."""
        ...

    @abstractmethod
    def record_export_error(self, task_id: UUID, error: str) -> None:
        """Record why exporting the task to Label Studio failed."""
        ...

    @abstractmethod
    def approve_batch(
        self,
        batch_id: UUID,
        *,
        actor: str,
        approved_task_ids: list[UUID],
    ) -> None:
        """Mark current synced revisions as approved after explicit human action."""
        ...

    @abstractmethod
    def sync_annotation(
        self,
        task_id: UUID,
        *,
        annotation_id: int | None,
        result: list[dict] | None,
        revision: int | None,
        updated_at,
        deleted: bool = False,
    ) -> None:
        """Persist current Label Studio annotation state for a task."""
        ...

    @abstractmethod
    def record_sync_error(self, task_id: UUID, error: str) -> None:
        """Record why syncing or approving the task's annotation failed."""
        ...

    @abstractmethod
    def approved_training_rows(self) -> list[dict]:
        """Return only approved current annotation revisions for model training."""
        ...


@dataclass(frozen=True)
class PoiCandidate:
    """A stored listing description waiting for POI extraction."""

    id: UUID
    description: str


class PoiPredictor(Protocol):
    """Client of a POI extraction service."""

    def predict(self, description: str) -> dict:
        """Extract POIs from one description; the result includes the serving ``model_version``."""
        ...


class PoiRepository(Protocol):
    """Storage for POI predictions, read and written without holding database locks."""

    def pending(self, model_version: str) -> Iterable[PoiCandidate]:
        """Yield candidates that have no prediction from ``model_version`` yet."""
        ...

    def save(self, candidate: PoiCandidate, prediction: dict) -> bool:
        """Store the prediction; return whether it was actually saved (counted as enriched)."""
        ...


class TeacherAnnotationProvider(ABC):
    """Asks a teacher model to pre-annotate a task so reviewers start from a prediction."""

    @abstractmethod
    def label(self, task: LabelingTask) -> TeacherAnnotation:
        """Generate one teacher annotation for the task snapshot."""
        ...


class ModelPredictor(Protocol):
    """A trained model that extracts entity spans from text."""

    def predict(self, text: str) -> list[dict]:
        """Return the predicted spans (start, end, category, confidence) for ``text``."""
        ...


class PredictionRepository(Protocol):
    """Storage for per-sample prediction records of an evaluation run."""

    def save(self, records: list[dict], destination: str) -> None:
        """Write the records to ``destination``."""
        ...

    def load(self, location: str) -> list[dict]:
        """Read back records previously saved at ``location``."""
        ...


class ExperimentRepository(Protocol):
    """Experiment tracker that keeps prediction files as artifacts of a run."""

    def log_predictions(self, run_id: str, file_path: str) -> None:
        """Attach the prediction file to the run."""
        ...

    def load_predictions(self, run_id: str, artifact_path: str) -> list[dict]:
        """Load the prediction records stored under ``artifact_path`` for the run."""
        ...


class GeoCatalogueSource(Protocol):
    """Downloads a complete catalogue into immutable source snapshots."""

    def fetch(self) -> tuple[list[GeoFeature], dict]:
        """Return validated features and a manifest identifying their raw snapshots."""
        ...


class GeoCatalogueRepository(Protocol):
    """Publishes complete releases without exposing partially imported data."""

    def publish(self, features: list[GeoFeature], manifest: dict) -> str:
        """Atomically activate a validated release and return its fingerprint."""
        ...

    def locate(self, longitude: float, latitude: float) -> list[dict]:
        """Return every active planning unit covering a WGS84 point."""
        ...


class PlanningUnitMapRepository(Protocol):
    """Read-only boundary and current-listing data for a pinned map release."""

    def map_data(self, assignments: list[PlanningUnitAssignment] | None) -> dict:
        """Return planning units and fresh accepted assignments for display."""
        ...


class LocationResolutionRepository(Protocol):
    """Versioned catalogue evidence and optimistic resolution output storage."""

    release_id: str
    bundle_id: str | None

    def index(self):
        """Build a source-name matching index from pinned reference versions."""
        ...

    def listings(self):
        """Yield active listing snapshots in bounded database pages."""
        ...

    def support(self, identities: tuple, radius: float) -> dict[str, float]:
        """Return metric spatial support per planning unit for a mention group."""
        ...

    def exists(self, listing_id: str, input_fingerprint: str) -> bool:
        """Check whether these exact resolver inputs already have a result."""
        ...

    def activate_policy(self, policy: dict) -> str:
        """Select the resolver context/model that may produce current map assignments."""
        ...

    def known_units(self) -> set[str]:
        """List valid target planning units in the pinned catalogue."""
        ...

    def save(self, listing, result: dict) -> bool:
        """Save only if listing source text, location and predictions are unchanged."""
        ...
