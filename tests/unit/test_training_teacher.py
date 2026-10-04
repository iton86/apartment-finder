"""
Tests for the provider-agnostic teacher pipeline: turning a normalized
TeacherOutput into a labels record, and the run / submit / collect
bookkeeping, driven by a fake provider so no SDK or network is involved.
"""

import argparse
import json

import pytest

from training import label_with_teacher as lwt
from training.jsonl import read_jsonl, write_jsonl
from training.teacher import PROVIDERS, provider_class
from training.teacher.base import (
    BatchNotSupportedError,
    BatchProgress,
    Outcome,
    TeacherOutput,
    TeacherProvider,
)

PARK = {
    "category": "park",
    "name": "Южен парк",
    "distance": {"value": None, "unit": "nearby"},
    "evidence": "до ЮЖЕН ПАРК",
}
GOOD = TeacherOutput(Outcome.COMPLETE, text=json.dumps({"pois": [PARK]}), usage={"in": 1})


def row(i: int = 0) -> dict:
    return {
        "id": f"imot.bg:{i}",
        "content_hash": f"{i:064x}",
        "title": "Продава 2-СТАЕН",
        "city": "София",
        "area": "Манастирски ливади",
        "url": f"https://www.imot.bg/{i}",
        "description": "Двустаен до ЮЖЕН ПАРК.",
    }


class FakeProvider(TeacherProvider):
    name = "fake"
    default_model = "fake-1"

    def __init__(self, model=None, effort=None, outputs=None):
        super().__init__(model, effort)
        self.outputs = outputs or {}
        self.labeled: list[str] = []
        self.submitted: list[list[str]] = []
        self.done = True

    def label(self, row):
        self.labeled.append(row["content_hash"])
        output = self.outputs.get(row["content_hash"], GOOD)
        if isinstance(output, Exception):
            raise output
        return output

    def submit_batch(self, rows, workdir):
        self.submitted.append([r["content_hash"] for r in rows])
        return {"batch_id": f"b{len(self.submitted)}"}

    def batch_progress(self, state):
        return BatchProgress(self.done, "ended" if self.done else "in_progress")

    def batch_results(self, state):
        # Only the first item comes back, to exercise "missing" handling.
        first = self.submitted[0][0]
        return {first: GOOD}


@pytest.fixture
def workspace(tmp_path):
    raw = tmp_path / "raw.jsonl"
    write_jsonl(raw, [row(0), row(1), row(2)])
    return argparse.Namespace(
        raw=raw,
        labels=tmp_path / "labels.jsonl",
        batches=tmp_path / "batches.json",
        limit=None,
        wait=False,
        save=False,
    )


class TestToRecord:
    def test_ok(self):
        record = lwt.to_record(row(), FakeProvider(), GOOD)
        assert record["status"] == "ok" and record["pois"] == [PARK]
        assert (record["provider"], record["model"]) == ("fake", "fake-1")

    def test_invented_evidence_is_kept_but_flagged(self):
        bad = {**PARK, "evidence": "до Борисова градина"}
        output = TeacherOutput(Outcome.COMPLETE, text=json.dumps({"pois": [bad]}))
        record = lwt.to_record(row(), FakeProvider(), output)
        assert record["status"] == "invalid" and record["pois"] == [bad]

    def test_schema_violation(self):
        output = TeacherOutput(Outcome.COMPLETE, text='{"pois": [{"category": "castle"}]}')
        record = lwt.to_record(row(), FakeProvider(), output)
        assert record["status"] == "error" and record["pois"] is None

    @pytest.mark.parametrize(
        ("outcome", "status"),
        [(Outcome.REFUSED, "refused"), (Outcome.TRUNCATED, "error"), (Outcome.FAILED, "error")],
    )
    def test_abnormal_outcomes(self, outcome, status):
        record = lwt.to_record(row(), FakeProvider(), TeacherOutput(outcome, detail="x"))
        assert record["status"] == status and record["pois"] is None


class TestRun:
    def test_labels_everything_then_nothing_on_rerun(self, workspace):
        provider = FakeProvider()
        lwt.cmd_run(workspace, provider)
        lwt.cmd_run(workspace, provider)
        assert len(provider.labeled) == 3
        assert [r["status"] for r in read_jsonl(workspace.labels)] == ["ok"] * 3

    def test_failed_rows_are_retried(self, workspace):
        lwt.cmd_run(workspace, FakeProvider(outputs={row(1)["content_hash"]: RuntimeError("x")}))
        retry = FakeProvider()
        lwt.cmd_run(workspace, retry)
        assert retry.labeled == [row(1)["content_hash"]]

    def test_another_model_relabels(self, workspace):
        lwt.cmd_run(workspace, FakeProvider())
        other = FakeProvider(model="fake-2")
        lwt.cmd_run(workspace, other)
        assert len(other.labeled) == 3

    def test_stops_after_consecutive_failures(self, workspace):
        failing = {row(i)["content_hash"]: RuntimeError("bad key") for i in range(3)}
        with pytest.raises(SystemExit, match="bad key"):
            lwt.cmd_run(workspace, FakeProvider(outputs=failing))


class TestBatch:
    def test_submit_then_collect(self, workspace, monkeypatch):
        provider = FakeProvider(effort="low")
        lwt.cmd_submit(workspace, provider)
        [entry] = json.loads(workspace.batches.read_text())
        assert entry["batch_id"] == "b1" and entry["effort"] == "low"

        # Pending items are not submitted twice.
        lwt.cmd_submit(workspace, provider)
        assert len(provider.submitted) == 1

        # collect rebuilds the provider from the batch entry.
        monkeypatch.setattr(lwt, "get_provider", lambda *a: provider)
        lwt.cmd_collect(workspace, None)
        records = read_jsonl(workspace.labels)
        assert [r["status"] for r in records] == ["ok", "error", "error"]
        assert "no result" in records[1]["problems"][0]
        assert json.loads(workspace.batches.read_text())[0]["collected"] is True

        # The two missing items go out again in the next batch.
        lwt.cmd_submit(workspace, provider)
        assert provider.submitted[1] == [row(1)["content_hash"], row(2)["content_hash"]]

    def test_unfinished_batch_is_left_for_later(self, workspace, monkeypatch):
        provider = FakeProvider()
        lwt.cmd_submit(workspace, provider)
        provider.done = False
        monkeypatch.setattr(lwt, "get_provider", lambda *a: provider)
        lwt.cmd_collect(workspace, None)
        assert read_jsonl(workspace.labels) == []
        assert json.loads(workspace.batches.read_text())[0]["collected"] is False


class TestProviderContract:
    def test_batch_methods_are_optional(self, workspace):
        class SyncOnly(TeacherProvider):
            name = "sync"
            default_model = "m"

            def label(self, row):
                return GOOD

        with pytest.raises(BatchNotSupportedError):
            SyncOnly().submit_batch([row()], workspace.batches.parent)

    def test_default_model_used_when_none_given(self):
        assert FakeProvider().model == "fake-1"
        assert FakeProvider(model="x").model == "x"

    def test_unknown_provider(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            provider_class("nope")

    @pytest.mark.parametrize("name", sorted(PROVIDERS))
    def test_registered_providers_load(self, name):
        try:
            cls = provider_class(name)
        except ImportError as e:  # that provider's SDK isn't installed here
            pytest.skip(str(e))
        assert cls.name == name and cls.default_model


class TestSubmitWithoutBatchSupport:
    def test_exits_with_hint_instead_of_traceback(self, workspace):
        class SyncOnly(FakeProvider):
            def submit_batch(self, rows, workdir):
                raise BatchNotSupportedError("sync has no batch support — use `run`")

        with pytest.raises(SystemExit, match="use `run`"):
            lwt.cmd_submit(workspace, SyncOnly())
        assert not workspace.batches.exists()


class TestResolveModelAndEffort:
    @pytest.fixture(autouse=True)
    def env(self, monkeypatch):
        monkeypatch.setenv("TEACHER_PROVIDER", "openai")
        monkeypatch.setenv("TEACHER_MODEL", "gpt-5.5")
        monkeypatch.setenv("TEACHER_EFFORT", "high")

    def test_env_applies_to_its_own_provider(self):
        assert lwt.resolve_model_and_effort("openai", None, None) == ("gpt-5.5", "high")

    def test_env_ignored_for_another_provider(self):
        pytest.importorskip("anthropic")
        assert lwt.resolve_model_and_effort("anthropic", None, None) == (None, "medium")

    def test_flags_win(self):
        assert lwt.resolve_model_and_effort("openai", "gpt-x", "low") == ("gpt-x", "low")

    def test_default_sends_no_effort(self):
        assert lwt.resolve_model_and_effort("openai", None, "default") == ("gpt-5.5", None)
