"""Opt-in MLflow tracking; checkpoints and source data stay on disk."""

import json
import math
from contextlib import contextmanager
from numbers import Real


@contextmanager
def track_training(args, trainer):
    """Own one run through training, best-model evaluation, and artifact saving."""
    if not getattr(args, "mlflow", False):
        yield
        return

    import mlflow
    from transformers import TrainerCallback

    class MetricsCallback(TrainerCallback):
        def on_log(self, training_args, state, control, logs=None, **kwargs):
            metrics = {
                key: float(value)
                for key, value in (logs or {}).items()
                if isinstance(value, Real) and math.isfinite(value)
            }
            mlflow.log_metrics(metrics, step=state.global_step)

    if mlflow.active_run() is not None:
        raise ValueError("End the active MLflow run before starting training")
    mlflow.set_tracking_uri(args.mlflow_tracking_uri)
    mlflow.set_experiment(args.mlflow_experiment)
    callback = MetricsCallback()
    with mlflow.start_run(run_name=args.run_name or args.output.name) as active:
        config = json.loads((args.output / "run_config.json").read_text())
        # Tracking URIs may contain credentials; do not publish them as parameters/artifacts.
        config.pop("mlflow_tracking_uri", None)
        mlflow.log_params({k: v for k, v in config.items() if k != "label_names"})
        mlflow.set_tag("model_directory", str((args.output / "model").resolve()))
        mlflow.log_dict(config, "run_config.json")
        mlflow.log_artifact(str(args.output / "split_manifest.json"))
        (args.output / "mlflow_run.json").write_text(
            json.dumps({"run_id": active.info.run_id, "experiment_id": active.info.experiment_id})
            + "\n"
        )
        trainer.add_callback(callback)
        try:
            yield
            metrics = json.loads((args.output / "validation_results.json").read_text())
            mlflow.log_metrics(
                {f"best_model_{k}": v for k, v in metrics.items()}, step=trainer.state.global_step
            )
            mlflow.log_artifact(str(args.output / "validation_results.json"))
            mlflow.log_artifact(str(args.output / "mlflow_run.json"))
        finally:
            trainer.remove_callback(callback)
