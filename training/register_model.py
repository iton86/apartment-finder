"""Package a saved encoder into the local MLflow model registry."""

import argparse
import hashlib
import json
import tempfile
from importlib.metadata import version
from pathlib import Path


def model_fingerprint(checkpoint: Path, max_length: int) -> str:
    """Identify weights, tokenizer, settings and inference implementation together."""
    checksum = hashlib.sha256()
    checksum.update(json.dumps({"max_length": max_length}, sort_keys=True).encode())
    package = Path(__file__).resolve().parents[1] / "src/apartment_finder"
    for relative in (
        "infrastructure/poi/mlflow_model.py",
        "infrastructure/poi/hf_predictor.py",
        "domain/evaluation.py",
    ):
        checksum.update((package / relative).read_bytes())
    for path in sorted(checkpoint.rglob("*")):
        if path.is_file():
            checksum.update(str(path.relative_to(checkpoint)).encode())
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    checksum.update(chunk)
    return checksum.hexdigest()


def main():
    """Log the checkpoint as an MLflow pyfunc model and register it under ``--name``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--tracking-uri", default="sqlite:///mlflow.db")
    parser.add_argument("--name", default="poi-encoder")
    args = parser.parse_args()
    import mlflow
    from mlflow.models import ModelSignature
    from mlflow.types import ColSpec, Schema

    from apartment_finder.infrastructure.poi.mlflow_model import PoiModel

    config = json.loads((args.run / "run_config.json").read_text())
    checkpoint = args.run / "model"
    if not checkpoint.is_dir():
        parser.error("Run must contain a saved model/ directory")
    settings = {
        "model_version": model_fingerprint(checkpoint, config["max_length"]),
        "max_length": config["max_length"],
    }
    mlflow.set_tracking_uri(args.tracking_uri)
    mlflow.set_experiment("poi-deployments")
    with tempfile.TemporaryDirectory() as directory, mlflow.start_run():
        path = Path(directory) / "settings.json"
        path.write_text(json.dumps(settings))
        info = mlflow.pyfunc.log_model(
            name="poi",
            python_model=PoiModel(),
            artifacts={"checkpoint": str(checkpoint.resolve()), "settings": str(path)},
            code_paths=[str(Path(__file__).resolve().parents[1] / "src/apartment_finder")],
            signature=ModelSignature(inputs=Schema([ColSpec("string", "description")])),
            pip_requirements=[
                f"{name}=={version(name)}" for name in ("mlflow", "torch", "transformers", "pandas")
            ],
            registered_model_name=args.name,
        )
        print(f"Model URI: models:/{args.name}/{info.registered_model_version}")
        print(f"Prediction version: {settings['model_version']}")


if __name__ == "__main__":
    main()
