"""Streamlit inspector for MLflow-linked POI validation and gold predictions."""
import html
import os
from pathlib import Path

import mlflow
import pandas as pd
import streamlit as st

from apartment_finder.domain.evaluation.metrics import compare, summarize
from apartment_finder.application.evaluation.compare_runs import compare_runs
from apartment_finder.infrastructure.evaluation.mlflow_repository import MlflowExperimentRepository

st.set_page_config(page_title="POI Prediction Inspector", layout="wide")
st.title("POI Prediction Inspector")
uri = st.sidebar.text_input("MLflow tracking URI", os.getenv("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db"))
mlflow.set_tracking_uri(uri)
client = mlflow.MlflowClient()
experiments = client.search_experiments()
if not experiments:
    st.info("No MLflow experiments found. Train with --mlflow first.")
    st.stop()
experiment = st.sidebar.selectbox("Experiment", experiments, format_func=lambda e: e.name)
runs = client.search_runs([experiment.experiment_id], max_results=200, order_by=["attributes.start_time DESC"])
if not runs:
    st.info("No runs in this experiment.")
    st.stop()
run = st.sidebar.selectbox("Run", runs, format_func=lambda r: f"{r.data.tags.get('mlflow.runName', r.info.run_id)} ({r.info.run_id[:8]})")
artifacts = client.list_artifacts(run.info.run_id, "inspection")
paths = [a.path for a in artifacts if a.path.endswith(".jsonl")]
if not paths:
    st.warning("No prediction artifacts. Run training.inspection with --log-mlflow first.")
    st.code("uv run --group training --group tracking python -m training.inspection checkpoints/YOUR_RUN --split validation --log-mlflow")
    st.stop()
path = st.sidebar.selectbox("Prediction artifact", paths)
@st.cache_data(show_spinner=False)
def load_predictions(tracking_uri, run_id, artifact):
    return MlflowExperimentRepository(tracking_uri).load_predictions(run_id, artifact)

rows = load_predictions(uri, run.info.run_id, path)
metrics = summarize(rows)
c1, c2, c3, c4 = st.columns(4)
for column, label, key in zip((c1, c2, c3, c4), ("Precision", "Recall", "F1", "Samples"),
                              ("precision", "recall", "f1", None), strict=True):
    column.metric(label, f"{metrics[key]:.3f}" if key else len(rows))
view = st.tabs(["Sample explorer", "Error analysis", "Run comparison"])
with view[0]:
    kind = st.selectbox("Filter", ["All", "Errors only", "False negatives", "False positives", "Correct only"])
    threshold = st.slider("Minimum prediction confidence", 0.0, 1.0, 0.0, 0.05)
    filtered = []
    for row in rows:
        predictions = [p for p in row["predictions"] if p.get("confidence", 1.0) >= threshold]
        score = compare(row["ground_truth"], predictions)
        if kind == "Errors only" and not score["errors"]:
            continue
        if kind == "False negatives" and not score["fn"]:
            continue
        if kind == "False positives" and not score["fp"]:
            continue
        if kind == "Correct only" and score["errors"]:
            continue
        filtered.append((row, predictions, score))
    st.caption(f"{len(filtered)} matching samples")
    if filtered:
        index = st.number_input("Sample index", 1, len(filtered), 1) - 1
        row, predictions, score = filtered[index]
        st.subheader(f"Listing {row['id']}")
        st.write(row["text"])
        left, right = st.columns(2)
        left.write("Ground truth")
        left.dataframe(pd.DataFrame(row["ground_truth"]), use_container_width=True)
        right.write("Predictions")
        right.dataframe(pd.DataFrame(predictions), use_container_width=True)
        st.write(f"TP: {score['tp']} · FP: {score['fp']} · FN: {score['fn']}")
        # Escape source text before inserting markup; labels originate from external data.
        for title, spans in (("Ground truth", row["ground_truth"]), ("Predictions", predictions)):
            text = row["text"]
            cursor, parts = 0, []
            for entity in sorted(spans, key=lambda e: (e["start"], e["end"])):
                start, end = entity["start"], entity["end"]
                if start < cursor or end > len(text) or start >= end:
                    continue
                parts.append(html.escape(text[cursor:start]))
                parts.append("<mark title='" + html.escape(entity["category"], quote=True) + "'>" +
                             html.escape(text[start:end]) + "</mark>")
                cursor = end
            parts.append(html.escape(text[cursor:]))
            st.markdown(f"**{title}**")
            st.markdown("<div style='white-space:pre-wrap'>" + "".join(parts) + "</div>",
                        unsafe_allow_html=True)
with view[1]:
    errors = []
    for row in rows:
        for error in compare(row["ground_truth"], row["predictions"])["errors"]:
            errors.append({"listing_id": row["id"], "type": error["type"],
                           "start": error["span"][0], "end": error["span"][1],
                           "category": error["span"][2],
                           "text": row["text"][error["span"][0]:error["span"][1]]})
    st.dataframe(pd.DataFrame(errors), use_container_width=True)
    st.download_button("Download errors CSV", pd.DataFrame(errors).to_csv(index=False).encode(),
                       "poi_errors.csv", "text/csv")
with view[2]:
    other = st.selectbox("Compare with", [r for r in runs if r.info.run_id != run.info.run_id],
                         format_func=lambda r: r.data.tags.get("mlflow.runName", r.info.run_id))
    if other:
        other_paths = [a.path for a in client.list_artifacts(other.info.run_id, "inspection")
                       if a.path.endswith(".jsonl") and a.path.endswith(Path(path).name)]
        if other_paths:
            comparison = load_predictions(uri, other.info.run_id, other_paths[0])
            diffs = compare_runs(rows, comparison)
            st.caption("Negative delta = improvement; only identical text and ground truth are compared.")
            st.dataframe(pd.DataFrame(diffs).sort_values("delta") if diffs else pd.DataFrame(),
                         use_container_width=True)
        else:
            st.info("The comparison run has no matching prediction artifact.")
