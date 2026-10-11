# Machine-learning pipeline

## Scope

The `training/` package is offline tooling. It exports descriptions from the
application database, supports annotation, and fine-tunes Hugging Face token
classification models. It is not included in the scraper image and does not
change listing records during training.

The implemented encoder predicts POI name spans and categories using BIO labels.
Its default checkpoint is `FacebookAI/xlm-roberta-base`; another compatible
encoder can be selected. The broader annotation schema includes distances, but
the encoder does not yet predict distances or the complete extraction JSON.

## Workflow

1. Export cleaned, deduplicated listing descriptions using read-only database
   access. Confirm the database destination through `APP_ENV`.
2. Optionally obtain teacher-model draft labels.
3. Review annotations in Label Studio and prepare human-labeled records.
4. Reserve a held-out gold set for final evaluation.
5. Fine-tune an encoder on the remaining records and inspect validation results.
6. Evaluate the saved model against gold records and test individual predictions.

The [annotation and training guide](../training/README.md) documents the exact
file formats, labeling commands, category schema, and export/import workflow.

## Running the encoder

After preparing the labeled dataset described in that guide:

```bash
uv sync --group training
make training-fit TRAINING_MODEL=FacebookAI/xlm-roberta-base \
  TRAINING_RUN=checkpoints/poi-encoder
make training-evaluate TRAINING_RUN=checkpoints/poi-encoder
make training-predict TRAINING_RUN=checkpoints/poi-encoder \
  TEXT_FILE=data/example-description.txt
```

Use a new output directory for each training run. Existing nonempty output
directories are rejected. For direct Python module invocations requiring ML or
provider libraries, retain the optional group explicitly:

```bash
uv run --group training python -m training.finetune --help
uv run --group training python -m training.evaluate_encoder --help
uv run --group training python -m training.predict_encoder --help
```

Training performs full encoder fine-tuning, not a placeholder or simulated
update. Memory requirements depend on the checkpoint, token length, precision,
and batch settings. The Makefile enables gradient checkpointing; it does not
guarantee a particular GPU will have sufficient memory.

## Dataset safeguards

- Training requires reviewed human labels by default. Teacher labels require an
  explicit opt-in and are confined to training.
- Near-duplicate groups are kept together when constructing train/validation
  splits; gold records and their near duplicates are excluded from those splits.
- At least two eligible non-gold groups are required to create a split.
- Fast-tokenizer offsets map character annotations to BIO token labels.
- Overlapping entity spans and overlength examples are rejected rather than
  silently dropping annotations through truncation.
- Evaluation checks dataset provenance against the recorded checksum.

## Artifacts and interpretation

A run records the saved model/tokenizer, training configuration, dataset checksum,
split manifest, checkpoints, and validation results. Separate gold evaluation
writes gold metrics. Preserve these artifacts together to make results traceable.

Prediction returns entity names, categories, and character offsets. This output
is not the full `PoiExtraction` schema and should not be presented as distance
extraction. Validation guides development; use the held-out gold evaluation to
assess generalization without selecting settings against that set.

Generated descriptions and annotations belong in `data/`; model artifacts belong
in `checkpoints/`. Both are Git-ignored. Provider labeling may transfer description
text outside the local environment and may incur charges; choose the provider
and input data deliberately.
