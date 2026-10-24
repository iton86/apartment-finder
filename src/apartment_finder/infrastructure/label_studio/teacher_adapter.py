import sys
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from apartment_finder.application.ports.teacher_annotation import TeacherAnnotationProvider
from apartment_finder.domain.labeling import LabelingTask, TeacherAnnotation, TeacherStatus

try:
    from training.label_with_teacher import label_safely, to_record
    from training.teacher.base import TeacherProvider
    from training.teacher.prompt import SYSTEM_PROMPT
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
    from training.label_with_teacher import label_safely, to_record
    from training.teacher.base import TeacherProvider
    from training.teacher.prompt import SYSTEM_PROMPT

PROMPT_VERSION = sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]


class ExistingTeacherAnnotationProvider(TeacherAnnotationProvider):
    def __init__(self, provider: TeacherProvider):
        self._provider = provider

    def label(self, task: LabelingTask) -> TeacherAnnotation:
        row = {
            "id": str(task.id),
            "content_hash": task.content_hash,
            "description": task.description_snapshot,
            "title": "",
            "city": "",
            "area": "",
        }
        record = to_record(row, self._provider, label_safely(self._provider, row))
        return TeacherAnnotation(
            provider=self._provider.name,
            model=self._provider.model,
            prompt_version=PROMPT_VERSION,
            status=TeacherStatus(record["status"]),
            result=record["pois"],
            problems=record["problems"],
            labeled_at=datetime.now(UTC),
        )
