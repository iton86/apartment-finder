"""A packaged tiny encoder must reload without the training package."""

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest


def test_register_and_reload_tiny_model(tmp_path, monkeypatch):
    mlflow = pytest.importorskip("mlflow")
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    tokenizers = pytest.importorskip("tokenizers")
    torch.set_num_threads(1)
    from training import register_model

    run = tmp_path / "run"
    checkpoint = run / "model"
    checkpoint.mkdir(parents=True)
    backend = tokenizers.Tokenizer(
        tokenizers.models.WordLevel(
            {"<pad>": 0, "<unk>": 1, "Южен": 2, "парк": 3}, unk_token="<unk>"
        )
    )
    backend.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tokenizer = transformers.PreTrainedTokenizerFast(
        tokenizer_object=backend, pad_token="<pad>", unk_token="<unk>"
    )
    tokenizer.save_pretrained(checkpoint)
    model = transformers.RobertaForTokenClassification(
        transformers.RobertaConfig(
            vocab_size=4,
            hidden_size=8,
            num_hidden_layers=1,
            num_attention_heads=2,
            intermediate_size=16,
            max_position_embeddings=64,
            pad_token_id=0,
            num_labels=3,
            id2label={0: "O", 1: "B-park", 2: "I-park"},
            label2id={"O": 0, "B-park": 1, "I-park": 2},
        )
    )
    model.save_pretrained(checkpoint)
    (run / "run_config.json").write_text(json.dumps({"max_length": 16}))
    monkeypatch.chdir(tmp_path)
    tracking = f"sqlite:///{tmp_path / 'tracking.db'}"
    monkeypatch.setattr(sys, "argv", ["register", str(run), "--tracking-uri", tracking])
    register_model.main()
    # A fresh interpreter cannot import repository training helpers from its cwd.
    script = """
import importlib.util
import mlflow
import pandas as pd
import sys
assert importlib.util.find_spec("training") is None
mlflow.set_tracking_uri(sys.argv[1])
model = mlflow.pyfunc.load_model("models:/poi-encoder/1")
result = model.predict(pd.DataFrame({"description": ["Южен парк " * 30]}))
assert len(result) == 1
assert len(result[0]["model_version"]) == 64
for poi in result[0]["pois"]:
    assert ("Южен парк " * 30)[poi["start"]:poi["end"]] == poi["name"]
"""
    result = subprocess.run(
        [sys.executable, "-c", script, tracking],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    client = mlflow.tracking.MlflowClient(tracking_uri=tracking)
    assert client.get_model_version("poi-encoder", "1").status == "READY"
    import os
    from urllib.error import URLError
    from urllib.request import urlopen

    from apartment_finder.infrastructure.poi.http_predictor import MlflowHttpPredictor

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (tmp_path / "server.log").open("w+") as log:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mlflow",
                "models",
                "serve",
                "-m",
                "models:/poi-encoder/1",
                "--env-manager",
                "local",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
            ],
            cwd=tmp_path,
            env={
                **os.environ,
                "MLFLOW_TRACKING_URI": tracking,
                "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
            },
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 90
            while True:
                try:
                    with urlopen(f"http://127.0.0.1:{port}/ping", timeout=1):
                        break
                except (URLError, TimeoutError):
                    if server.poll() is not None or time.monotonic() > deadline:
                        log.seek(0)
                        pytest.fail(log.read())
                    time.sleep(0.2)
            response = MlflowHttpPredictor(f"http://127.0.0.1:{port}").predict("Южен парк")
            assert len(response["model_version"]) == 64
        finally:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


def test_model_fingerprint_changes_with_settings_and_weights(tmp_path):
    from training.register_model import model_fingerprint

    weights = tmp_path / "weights.bin"
    weights.write_bytes(b"first")
    original = model_fingerprint(tmp_path, 256)
    assert model_fingerprint(tmp_path, 256) == original
    assert model_fingerprint(tmp_path, 512) != original
    weights.write_bytes(b"second")
    assert model_fingerprint(tmp_path, 256) != original
