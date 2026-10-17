import argparse
import json

import pytest

from training.tracking import track_training


def test_disabled_tracking_needs_no_trainer_or_mlflow():
    with track_training(argparse.Namespace(), None):
        pass


def test_failed_training_closes_run(tmp_path, monkeypatch):
    mlflow = pytest.importorskip("mlflow")
    pytest.importorskip("transformers")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "run_config.json").write_text(json.dumps({"dataset_sha256": "abc"}))
    (tmp_path / "split_manifest.json").write_text("{}")

    class Trainer:
        def add_callback(self, callback):
            self.callback = callback

        def remove_callback(self, callback):
            assert self.callback is callback
            self.callback = None

    trainer = Trainer()
    args = argparse.Namespace(
        mlflow=True,
        mlflow_tracking_uri=f"sqlite:///{tmp_path / 'tracking.db'}",
        mlflow_experiment="failure-test",
        run_name="failed",
        output=tmp_path,
    )
    with pytest.raises(RuntimeError, match="training failed"):
        with track_training(args, trainer):
            raise RuntimeError("training failed")
    info = json.loads((tmp_path / "mlflow_run.json").read_text())
    assert mlflow.tracking.MlflowClient().get_run(info["run_id"]).info.status == "FAILED"
    assert mlflow.active_run() is None
    assert trainer.callback is None
