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


def test_rejects_conflicting_name_spans():
    example = row("до Южен парк", pois=[poi(), {**poi(), "category": "other"}])
    with pytest.raises(ValueError, match="overlapping"):
        name_spans(example)


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


def test_real_roberta_training_and_reload(tmp_path, monkeypatch, capsys):
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
        )
    )
    trained = transformers.AutoModelForTokenClassification.from_pretrained(output / "model")
    assert not torch.equal(before, trained.roberta.embeddings.word_embeddings.weight)
    assert trained.config.label2id == LABEL2ID
    assert (output / "validation_results.json").exists()
    assert not (output / "gold_metrics.json").exists()
    assert json.loads((output / "split_manifest.json").read_text())["gold"]
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
