"""Data integrity and a real offline RoBERTa optimizer/save/reload smoke test."""

import argparse
import json

import pytest

from training.token_data import LABEL2ID, encode_row, entities, name_spans, split_rows


def row(text, split="train", pois=None, source="human"):
    return {
        "id": text,
        "content_hash": text,
        "description": text,
        "split": split,
        "label_source": source,
        "pois": pois or [],
    }


def poi(name="Южен парк", evidence="до Южен парк"):
    return {
        "name": name,
        "evidence": evidence,
        "category": "park",
        "distance": {"value": None, "unit": "nearby"},
    }


def test_gold_neighbours_are_excluded_and_split_is_reproducible():
    rows = [
        row("до Южен парк", "gold", [poi()]),
        row("До Южен парк", pois=[poi()]),
        row("Просторен апартамент"),
        row("Нов ремонт с тераса"),
        row("Тихо жилище"),
    ]
    a = split_rows(rows, 0.2, 42)
    b = split_rows(list(reversed(rows)), 0.2, 42)
    assert len(a["excluded"]) == 1
    assert a["excluded"][0]["description"] == "До Южен парк"
    for split in a:
        assert {r["id"] for r in a[split]} == {r["id"] for r in b[split]}
    assert a["train"] and a["validation"]


def test_rejects_teacher_gold():
    with pytest.raises(ValueError, match="human reviewed"):
        split_rows([row("Текст", "gold", source="teacher")], 0.2, 42)


def test_locates_name_inside_evidence_not_an_earlier_mention():
    example = row("Южен парк. Жилище до Южен парк.", pois=[poi()])
    assert name_spans(example) == [(21, 30, "park")]


@pytest.mark.parametrize("categories", [("park", "other"), ("other", "park")])
def test_keeps_first_category_for_conflicting_name_spans(categories):
    example = row("до Южен парк", pois=[{**poi(), "category": c} for c in categories])
    assert name_spans(example) == [(3, 12, categories[0])]


def test_skips_partial_overlaps_and_keeps_adjacent_names_in_text_order():
    example = row(
        "Южен парк София",
        pois=[
            poi("София", "София"),
            poi("парк", "парк"),
            poi("Южен парк", "Южен парк"),
            poi("парк", "парк"),
        ],
    )
    assert name_spans(example) == [(5, 9, "park"), (10, 15, "park")]


def test_bio_boundaries_and_orphan_inside_tag():
    assert entities(["I-park", "I-park", "B-park", "O"]) == {(0, 2, "park"), (2, 3, "park")}


def test_alignment_and_overlength():
    def tokenizer(text, **kwargs):
        return {
            "input_ids": [0, 4, 5, 6, 2],
            "offset_mapping": [(0, 0), (0, 2), (3, 7), (8, 12), (0, 0)],
        }

    example = row("до Южен парк", pois=[poi()])
    encoded = encode_row(example, tokenizer, 5)
    assert encoded["labels"] == [-100, 0, LABEL2ID["B-park"], LABEL2ID["I-park"], -100]
    with pytest.raises(ValueError, match="exceeds"):
        encode_row(example, tokenizer, 4)


@pytest.mark.parametrize("tracking", [False, True])
def test_real_roberta_training_and_reload(tmp_path, monkeypatch, capsys, tracking):
    if tracking:
        mlflow = pytest.importorskip("mlflow")
    monkeypatch.chdir(tmp_path)
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    pytest.importorskip("accelerate")
    tokenizers = pytest.importorskip("tokenizers")
    from training.finetune import run
    from training.jsonl import write_jsonl

    torch.set_num_threads(1)
    backend = tokenizers.Tokenizer(
        tokenizers.models.WordLevel(
            {"<s>": 0, "<pad>": 1, "</s>": 2, "<unk>": 3, "до": 4, "Южен": 5, "парк": 6},
            unk_token="<unk>",
        )
    )
    backend.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tokenizer = transformers.PreTrainedTokenizerFast(
        tokenizer_object=backend,
        unk_token="<unk>",
        pad_token="<pad>",
        bos_token="<s>",
        eos_token="</s>",
        model_max_length=32,
    )
    model_path = tmp_path / "base"
    tokenizer.save_pretrained(model_path)
    model = transformers.RobertaForTokenClassification(
        transformers.RobertaConfig(
            vocab_size=7,
            hidden_size=16,
            num_hidden_layers=1,
            num_attention_heads=2,
            intermediate_size=32,
            max_position_embeddings=34,
            pad_token_id=1,
            num_labels=len(LABEL2ID),
            id2label={v: k for k, v in LABEL2ID.items()},
            label2id=LABEL2ID,
        )
    )
    model.save_pretrained(model_path)
    before = model.roberta.embeddings.word_embeddings.weight.detach().clone()
    labels = tmp_path / "labels.jsonl"
    write_jsonl(
        labels,
        [
            row("до Южен парк", pois=[poi()]),
            row("ново жилище"),
            row("светъл дом"),
            row("близо до Лидл", split="gold"),
        ],
    )
    output = tmp_path / "run"
    run(
        argparse.Namespace(
            labels=labels,
            model=str(model_path),
            output=output,
            epochs=1,
            learning_rate=0.001,
            batch_size=1,
            gradient_accumulation_steps=1,
            max_length=32,
            validation_fraction=0.2,
            seed=42,
            gradient_checkpointing=False,
            cpu=True,
            allow_teacher=False,
            mlflow=tracking,
            mlflow_tracking_uri=f"sqlite:///{tmp_path / 'mlflow.db'}",
            mlflow_experiment="smoke-test",
            run_name="tiny-roberta",
        )
    )
    trained = transformers.AutoModelForTokenClassification.from_pretrained(output / "model")
    assert not torch.equal(before, trained.roberta.embeddings.word_embeddings.weight)
    assert trained.config.label2id == LABEL2ID
    assert (output / "validation_results.json").exists()
    assert not (output / "gold_metrics.json").exists()
    assert json.loads((output / "split_manifest.json").read_text())["gold"]
    if tracking:
        info = json.loads((output / "mlflow_run.json").read_text())
        client = mlflow.tracking.MlflowClient()
        tracked = client.get_run(info["run_id"])
        assert tracked.info.status == "FINISHED"
        assert (
            tracked.data.params["dataset_sha256"]
            == json.loads((output / "run_config.json").read_text())["dataset_sha256"]
        )
        assert "eval_f1" in tracked.data.metrics
        assert "best_model_eval_f1" in tracked.data.metrics
        assert not any("gold" in key for key in tracked.data.metrics)
        assert "mlflow_tracking_uri" not in tracked.data.params
        assert {a.path for a in client.list_artifacts(info["run_id"])} == {
            "run_config.json",
            "split_manifest.json",
            "validation_results.json",
            "mlflow_run.json",
        }
        assert mlflow.active_run() is None
    from training import evaluate_encoder, predict_encoder

    monkeypatch.setattr("sys.argv", ["evaluate", str(output), "--cpu"])
    evaluate_encoder.main()
    assert "gold_f1" in json.loads((output / "gold_metrics.json").read_text())
    text_file = tmp_path / "description.txt"
    text_file.write_text("до Южен парк", encoding="utf-8")
    capsys.readouterr()
    monkeypatch.setattr("sys.argv", ["predict", str(output), "--text-file", str(text_file)])
    predict_encoder.main()
    assert "entities" in json.loads(capsys.readouterr().out)
    labels.write_text(labels.read_text() + "\n")
    monkeypatch.setattr("sys.argv", ["evaluate", str(output), "--cpu"])
    with pytest.raises(SystemExit):
        evaluate_encoder.main()
    with pytest.raises(ValueError, match="empty"):
        run(
            argparse.Namespace(
                labels=labels,
                model=str(model_path),
                output=output,
                epochs=1,
                learning_rate=0.001,
                batch_size=1,
                gradient_accumulation_steps=1,
                max_length=32,
                validation_fraction=0.2,
                seed=42,
                gradient_checkpointing=False,
                cpu=True,
                allow_teacher=False,
            )
        )


def test_entity_metrics_do_not_reward_outside_tokens():
    np = pytest.importorskip("numpy")
    from types import SimpleNamespace

    from training.token_data import compute_metrics

    labels = np.array([[-100, LABEL2ID["B-park"], LABEL2ID["I-park"], 0]])
    logits = np.zeros((1, 4, len(LABEL2ID)))
    assert compute_metrics(SimpleNamespace(predictions=logits, label_ids=labels))["f1"] == 0
    logits[0, 1, LABEL2ID["B-park"]] = 1
    logits[0, 2, LABEL2ID["I-park"]] = 1
    assert compute_metrics(SimpleNamespace(predictions=logits, label_ids=labels))["f1"] == 1


@pytest.mark.parametrize("suffix", [".", ",", "!", "”", " "])
def test_alignment_accepts_token_edge_punctuation(suffix):
    text = "трамвай 7" + suffix
    example = row(text, pois=[poi("трамвай 7", "трамвай 7")])

    def tokenizer(text, **kwargs):
        return {"input_ids": [4, 5], "offset_mapping": [(0, 7), (8, len(text))]}

    assert encode_row(example, tokenizer, 512)["labels"] == [LABEL2ID["B-park"], LABEL2ID["I-park"]]


def test_alignment_rejects_extra_word_characters():
    example = row("трамвай 74", pois=[poi("трамвай 7", "трамвай 7")])

    def tokenizer(text, **kwargs):
        return {"input_ids": [4, 5], "offset_mapping": [(0, 7), (8, 10)]}

    with pytest.raises(ValueError, match="conflicts with token '74'"):
        encode_row(example, tokenizer, 512)


def test_long_row_chunks_preserve_tokens_and_complete_entities():
    from training.token_data import encode_chunks

    text = "а б в г Южен парк д е"
    example = row(text, pois=[poi("Южен парк", "Южен парк")])

    def tokenizer(text, **kwargs):
        import re

        offsets = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
        return {
            "input_ids": [0, *range(3, 3 + len(offsets)), 2],
            "attention_mask": [1] * (len(offsets) + 2),
            "offset_mapping": [(0, 0), *offsets, (0, 0)],
        }

    chunks = encode_chunks(example, tokenizer, 7)
    assert [len(c["input_ids"]) for c in chunks] == [6, 6]
    assert [i for c in chunks for i in c["input_ids"][1:-1]] == list(range(3, 11))
    assert all(c["labels"][0] == c["labels"][-1] == -100 for c in chunks)
    assert chunks[1]["labels"][1:3] == [LABEL2ID["B-park"], LABEL2ID["I-park"]]
    assert encode_chunks(example, tokenizer, 20) == [encode_row(example, tokenizer, 20)]
    with pytest.raises(ValueError, match="one POI name exceeds"):
        encode_chunks(example, tokenizer, 3)
