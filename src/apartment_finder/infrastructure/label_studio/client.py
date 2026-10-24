from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from apartment_finder.application.ports.label_studio import LabelStudioGateway
from apartment_finder.domain.labeling import LabelStudioTaskPayload, LabelStudioTaskState

logger = logging.getLogger(__name__)


class LabelStudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class LabelStudioSettings:
    base_url: str
    api_token: str
    project_id: int


class LabelStudioClient(LabelStudioGateway):
    def __init__(self, *, base_url: str, api_token: str):
        self._base_url = base_url.rstrip("/") + "/"
        self._api_token = api_token

    def version(self) -> dict:
        return self._request("GET", "api/version")

    def import_tasks(
        self,
        project_id: int,
        tasks: list[LabelStudioTaskPayload],
    ) -> dict[str, int]:
        if not tasks:
            return {}

        before = self._task_ids_by_labeling_task_id(project_id)
        payloads_by_id = {str(task.task_id): task for task in tasks}
        for task_id, existing in before.items():
            expected = payloads_by_id.get(task_id)
            if expected and not task_data_matches(existing.data, expected.data):
                raise LabelStudioError(
                    f"Existing Label Studio task {existing.task_id} for {task_id} "
                    "does not match the selected text snapshot"
                )
        missing = [task for task in tasks if str(task.task_id) not in before]
        if missing:
            payload = [
                {
                    "data": task.data,
                    "predictions": task.predictions,
                }
                for task in missing
            ]
            self._request("POST", f"api/projects/{project_id}/import", payload)

        after = self._task_ids_by_labeling_task_id(project_id)
        for task_id, expected in payloads_by_id.items():
            existing = after.get(task_id)
            if existing and not task_data_matches(existing.data, expected.data):
                raise LabelStudioError(
                    f"Imported Label Studio task {existing.task_id} for {task_id} "
                    "does not match the selected text snapshot"
                )
        return {
            str(task.task_id): after[str(task.task_id)].task_id
            for task in tasks
            if str(task.task_id) in after
        }

    def get_task(self, task_id: int) -> LabelStudioTaskState:
        payload = self._request("GET", f"api/tasks/{task_id}")
        return _state_from_payload(payload)

    def create_annotation(
        self,
        task_id: int,
        *,
        result: list[dict],
        completed_by: int | None = None,
    ) -> dict:
        payload: dict = {"result": result}
        if completed_by is not None:
            payload["completed_by"] = completed_by
        return self._request("POST", f"api/tasks/{task_id}/annotations/", payload)

    def _task_ids_by_labeling_task_id(self, project_id: int) -> dict[str, LabelStudioTaskState]:
        task_ids: dict[str, LabelStudioTaskState] = {}
        page = 1
        while True:
            query = urlencode({"project": project_id, "page": page, "page_size": 100})
            payload = self._request("GET", f"api/tasks?{query}")
            if isinstance(payload, list):
                results = payload
                next_page = None
            else:
                results = payload.get("tasks") or payload.get("results") or []
                next_page = payload.get("next")
            for task in results:
                data = task.get("data") or {}
                labeling_task_id = data.get("labeling_task_id")
                if labeling_task_id and task.get("id") is not None:
                    task_ids[str(labeling_task_id)] = _state_from_payload(task)
            if not next_page and (isinstance(payload, list) or not payload.get("next")):
                break
            page += 1
        return task_ids

    def _request(self, method: str, path: str, payload: dict | list | None = None):
        body = None
        headers = {
            "Authorization": f"Token {self._api_token}",
            "Accept": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            urljoin(self._base_url, path),
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=30) as response:  # noqa: S310 - configured local/service URL
                data = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LabelStudioError(
                f"Label Studio {method} {path} failed: {exc.code} {detail}"
            ) from exc
        if not data:
            return {}
        return json.loads(data.decode("utf-8"))


def _state_from_payload(payload: dict) -> LabelStudioTaskState:
    return LabelStudioTaskState(
        task_id=int(payload["id"]),
        data=payload.get("data") or {},
        predictions=payload.get("predictions") or [],
        annotations=payload.get("annotations") or [],
        updated_at=_parse_dt(payload.get("updated_at")),
    )


def task_data_matches(actual: dict, expected: dict) -> bool:
    return (
        actual.get("labeling_task_id") == expected.get("labeling_task_id")
        and actual.get("content_hash") == expected.get("content_hash")
        and actual.get("text") == expected.get("text")
    )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
