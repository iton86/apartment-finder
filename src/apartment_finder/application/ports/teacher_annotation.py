from abc import ABC, abstractmethod

from apartment_finder.domain.labeling import LabelingTask, TeacherAnnotation


class TeacherAnnotationProvider(ABC):
    @abstractmethod
    def label(self, task: LabelingTask) -> TeacherAnnotation:
        """Generate one teacher annotation for the task snapshot."""
        ...
