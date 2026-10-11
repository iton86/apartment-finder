# POI extraction — training pipeline

Offline tools for annotating listing descriptions and fine-tuning encoders for
point-of-interest names and categories. The annotation format in
[schema.py](schema.py) also supports distances; distance prediction is not yet
implemented by the encoder.

See the [project documentation](../docs/README.md) and
[machine-learning overview](../docs/machine-learning.md) for architecture, scope,
and artifact handling.

Everything here is offline tooling: it reads the app's database but ships
nowhere. Generated files live in `data/` (gitignored — it holds scraped ad
text).

## Step 2 — Export descriptions

```bash
make up                 # local Postgres
make training-export    # -> data/raw.jsonl + data/raw_report.json
```

Check `data/raw_report.json` before going further. `top_areas` should not be
dominated by one neighbourhood, or the model memorises that neighbourhood's
parks and malls instead of learning to read ads. Scrape more search URLs
until you have ~2,000 rows with no area above ~10%.

## Step 3 — Label

### 3a. Teacher pre-labels (costs money)

The teacher is pluggable. Pick it in `.env` (or per command with
`--provider`, `--model`, `--effort`):

```bash
TEACHER_PROVIDER=openai        # openai | anthropic | gemini | groq | ollama
# TEACHER_MODEL=gpt-5.5        # default: the provider's own default
# TEACHER_EFFORT=medium        # passed as-is; "default" sends none

OPENAI_API_KEY=sk-...          # openai
# ANTHROPIC_API_KEY=sk-ant-... # anthropic (or `ant auth login`)
# GEMINI_API_KEY=...           # gemini — free
# GROQ_API_KEY=gsk_...         # groq — free
```

`TEACHER_MODEL` and `TEACHER_EFFORT` only apply to `TEACHER_PROVIDER`. So
`--provider gemini` uses Gemini's defaults, not your OpenAI model.

| Provider | Cost | Default model | Batch | Credentials |
|---|---|---|---|---|
| `openai` | paid | `gpt-5.5` | yes (≤ 24 h) | `OPENAI_API_KEY` from platform.openai.com — a ChatGPT Plus/Pro subscription does **not** include API access |
| `anthropic` | paid | `claude-opus-5` | yes (usually < 1 h) | `ANTHROPIC_API_KEY`, or `ant auth login` |
| `gemini` | **free tier** | `gemini-3.5-flash-lite` | no — `run` | `GEMINI_API_KEY` from aistudio.google.com/apikey, no billing |
| `groq` | **free plan** | `openai/gpt-oss-120b` | no — `run` | `GROQ_API_KEY` from console.groq.com/keys |
| `ollama` | **free, local** | `qwen2.5:7b` | no — `run` | none; install ollama.com, `ollama pull qwen2.5:7b` |
| other OpenAI-compatible (vLLM, LM Studio, OpenRouter) | varies | set `TEACHER_MODEL` | usually no | `TEACHER_PROVIDER=openai`, `OPENAI_BASE_URL=...`, `TEACHER_EFFORT=default` |

**Free providers are for testing the pipeline and for reference labels, not
the final training set.**

- **Slow on purpose.** Requests are spaced out to stay under the free
  limits: about 10 per minute on Gemini, and about 3 per minute on Groq, whose
  8K tokens/min is the real limit. 386 ads take roughly 40 minutes on Gemini
  and 2 hours on Groq. If you still hit 429s, set
  `TEACHER_MIN_INTERVAL=<seconds>`.
- **Daily quotas.** Gemini Flash-Lite allows a few hundred requests per day;
  Groq allows 1K. When the quota runs out, `run` stops after 3 failures in a
  row. Re-run it the next day and it continues where it stopped.
- **Data use.** Free tiers may use what you send to improve their models.
  The ads are public and phone numbers are stripped, but keep it in mind.
- **Quality.** Smaller models follow the rules less reliably. Expect more
  `invalid` rows, and compare against a paid teacher on the same ads before
  trusting them.
- **Ollama on the GTX 1060.** A 7B model at 4-bit (~4.7 GB) fits, but don't
  run it while training: the two compete for the 6 GB of VRAM.

Free-tier limits change often. The defaults were set in October 2026. Check
your current ones at aistudio.google.com/rate-limit and
console.groq.com/settings/limits.

Compare teachers on the same ads, e.g. `try --provider gemini --limit 5`
next to `try --limit 5`. Labels remember their provider and model, so you
can label with several and keep them apart.

```bash
make label-try          # 3 ads, printed — read them critically
```

Tune `SYSTEM_PROMPT` in [teacher/prompt.py](teacher/prompt.py) until `try`
output matches the rules you want. The prompt is shared by every provider,
so switching models never changes the rules. Then either:

```bash
make label-submit       # every ad not yet labeled, as one batch (half price)
make label-collect      # waits for it, appends data/teacher_labels.jsonl
# or
make label-run          # one request per ad: any provider (the only option for free ones)
```

Each label is checked: `ok` passed every check, `invalid` has a problem a
reviewer can fix (usually an evidence quote that isn't verbatim),
`error`/`refused` is retried the next time you label.

Labels remember their provider and model. Switching model labels everything
again, appending to the same file; `label-tasks` uses the most recent label
for each ad. Changing `SYSTEM_PROMPT` does not relabel anything. Delete
`data/teacher_labels.jsonl` and `data/teacher_batches.json` to start over.

**Adding a provider** (Mistral, another hosted API, ...): subclass
`TeacherProvider` in `teacher/<name>_provider.py`. Implement `label()`, and the
three batch methods if the API has a batch endpoint. Then register it in
`PROVIDERS` in [teacher/\_\_init\_\_.py](teacher/__init__.py). The contract
and a checklist are in [teacher/base.py](teacher/base.py). For an
OpenAI-compatible service it's even shorter: one subclass with an `Endpoint`
in [teacher/openai_compatible.py](teacher/openai_compatible.py). Parsing,
validation and retries are shared, so an adapter only maps one request and
response.

### 3b. Human review in Label Studio

```bash
make label-studio       # http://localhost:8080, admin@localhost.dev / local_dev_only
make label-tasks        # data/label_studio_config.xml + data/ls_tasks.json
```

One-time project setup in the UI:

1. **Create Project** → name it, e.g. "POI review".
2. **Labeling Setup** → **Custom template** → **Code**, paste
   `data/label_studio_config.xml`, save.
3. **Import** → upload `data/ls_tasks.json`.
4. Settings → **Annotation** → turn on **Use predictions to prelabel tasks**,
   so the teacher's spans appear ready to edit.

#### Labeling guide

**What a POI is.** A place near the property that the ad identifies by a
proper name or a number: "спирка на автобус 204", "33 СУ", "метростанция
Джеймс Баучер", "Южен парк", "супермаркет Лидл", "бул. България". A bare
"спирка", "училище", "метростанция" or "магазини" is not a POI, even with a
distance. Leave those unlabeled. Neither is the building's own street
address.

**One POI = one highlighted span.** The span is the evidence: a contiguous
piece of the ad containing the place's name **and** the words that say how
far it is. Each span gets:

| Field | Where | Rule |
|---|---|---|
| category | label you pick before highlighting | closest fit; `other` if none fits |
| name | side panel, **required** | copied from the span, only capitalization fixed ("ЮЖЕН ПАРК" → "Южен парк"); keep a number's word ("автобус 204", not "204") |
| unit | side panel | `m`, `km`, `min_walk` ("пеша"), `min_drive` ("с кола"), `min_transit` ("с градски транспорт"), `min_unspecified` (minutes, no mode), `nearby` (no number: "до", "в близост до", "между") |
| value | side panel | the number as written; upper bound of a range ("5-10 мин" → 10); empty for `nearby` |

**Steps for one ad**

1. **Gold first.** Filter `split` = `gold` (it's also shown at the top of each
   task). Gold is the test set, so read every word.
2. Read `review_notes`. It lists teacher POIs that couldn't be placed in the
   text, for you to draw by hand.
3. Check each teacher span: click it (or click it in the **Regions** list on
   the right), then fix its label, name, unit or value in the side panel.
   Delete wrong ones with the Delete key.
4. Add missing POIs. Press Esc to deselect, click a category label, then drag
   over the text.
5. Click **Submit**. If the ad has no identifiable POIs, delete every span and
   submit it empty: "nothing here" is a real label the model needs. Use
   **Skip** for ads too unclear to judge; they're left out.

**Tricky cases**

- **Distance not next to the place.** Highlight one span from the distance
  words to the place name, even across other words. It's fine for several
  spans to overlap like this:

  > ***На 5 минути пеша** се намират метростанция **Джеймс Баучер**, 119 СУ и
  > супермаркет Лидл.*

  - metro_station: span "На 5 минути пеша се намират метростанция Джеймс Баучер", min_walk 5
  - school: span "На 5 минути пеша се намират метростанция Джеймс Баучер, 119 СУ", min_walk 5
  - supermarket: span "На 5 минути пеша … и супермаркет Лидл", min_walk 5

  Keep spans within one sentence when you can. If the distance only appears
  in a different sentence, the span may cover both, but no more than that.
- **One phrase covering several places** ("между Южен парк и НДК", "до
  метростанция Джеймс Баучер и Южен парк"). Create **one region per place, all
  over the same text**: highlight the phrase, set it up for the first place,
  press Esc, pick the next label, and highlight the same phrase again. Each
  region gets its own name. "между" (between) gives no number, so the unit is
  `nearby`.
- **Different distances in one sentence** ("на 300 м от метрото Джеймс Баучер
  и на 10 мин от МОЛ България"). Use separate spans, each with its own
  distance.
- **Same place mentioned twice.** Label it once, at the most specific mention
  (a number beats `nearby`).
- **Agency lists** ("В близост до имота ще намерите: … Училище: I m smart").
  Each named item is a POI with unit `nearby`. Highlight just that line.
- **Unsure whether a distance applies to a place?** Only use the number when
  the ad clearly ties the two together. Otherwise label the place `nearby`.

The span must be the ad's exact text. Drag-select it rather than typing it,
and include the whole name: the export checks that the name appears inside
its span.

`from-export` rejects a POI whose name is missing, generic (no digit and no
capital letter), or not found in its highlighted text, and tells you which
task to fix.

When you're done (or at any checkpoint): **Export → JSON** → save as
`data/ls_export.json`, then

```bash
uv run python -m training.label_studio from-export data/ls_export.json
# optionally: --include-teacher  fills unreviewed TRAIN ads with clean teacher labels
```

Rows that fail validation (e.g. a minutes unit with no value) are listed with
their Label Studio task id. Fix them, re-export, and re-run. The output
`data/labels.jsonl` is what Step 4 trains on.

New ads later: re-run `training-export` → `label-submit` → `label-collect`,
export the project as JSON once more, then build a file with only the new ads:

```bash
uv run python -m training.label_studio to-tasks --exclude data/ls_export.json
```

Import that `ls_tasks.json` into the same project. Label Studio doesn't
de-duplicate imports, so always pass `--exclude`. If an ad does end up
imported twice, `from-export` keeps one row for it.

## Step 4 — Fine-tune a POI encoder (RoBERTa / XLM-RoBERTa)

This runnable baseline learns **POI names and categories** with BIO token
classification. It reads `data/labels.jsonl` from Step 3b. It does **not**
learn distance values, units, or name-to-distance relations, and its output
is not the full `PoiExtraction` JSON schema. Overlapping evidence is fine;
overlapping names are rejected because one BIO tag cannot represent two
entities. Only the occurrence selected by the evidence is annotated; review
repeated mentions carefully because other occurrences become negative tokens.

Install the existing optional training group (the scraper does not import it):

```bash
uv sync --group training
uv run --group training python -m training.finetune \
  --model FacebookAI/xlm-roberta-base \
  --labels data/labels.jsonl \
  --output checkpoints/poi-xlm-roberta-v1 \
  --batch-size 1 --gradient-accumulation-steps 8 \
  --gradient-checkpointing
```

XLM-RoBERTa is the multilingual default for Bulgarian descriptions. Other
Transformers encoder checkpoints with fast tokenizers and token-classification
support can be supplied with `--model`, including `FacebookAI/roberta-base`
(for English). A local checkpoint directory also works. The first use of a
Hub model downloads its tokenizer and weights; training data is not uploaded,
Hub publishing is disabled, and external experiment reporting is disabled.
See the [XLM-RoBERTa model card](https://huggingface.co/FacebookAI/xlm-roberta-base)
and [token classification guide](https://huggingface.co/docs/transformers/tasks/token_classification).

This is full-parameter fine-tuning, not LoRA/quantization. Full precision is
used by default. XLM-RoBERTa can exceed a 6 GB GPU's memory even at batch size
one; gradient checkpointing helps but does not guarantee it fits. Use `--cpu`
(with sufficient system RAM), a smaller language-appropriate checkpoint, or
more GPU memory. `--epochs`, `--learning-rate`, `--max-length`, and `--seed`
are configurable. Training defaults to three epochs and learning rate 2e-5.

Data safeguards:

- Input rows are validated using the existing POI schema and evidence checks.
- Near-duplicate descriptions are clustered before splitting. Non-gold rows
  in a gold cluster are excluded entirely; remaining clusters stay together.
- Validation is selected deterministically from human-reviewed non-gold
  clusters. Gold never selects checkpoints. At least two independent
  non-gold clusters are required, including a human-reviewed validation group.
- Teacher rows require `--allow-teacher`; they cannot enter gold or validation.
- Text exceeding `--max-length` fails explicitly, rather than silently losing
  annotations. Split/review long descriptions upstream, or use a checkpoint
  with a larger context and adjust the limit. Automatic chunking is not implemented.
- Existing nonempty output directories are refused. Use a new versioned path.

The run directory contains `model/` (best validation checkpoint and tokenizer),
epoch checkpoints, `run_config.json` (including the input-file checksum),
`split_manifest.json`, and `validation_results.json`. Precision, recall, and F1
measure exact category/span matches at token boundaries, not distance quality.
Scores of zero are reported when neither predictions nor references contain
entities. These are baseline metrics, not evidence of deployment readiness.

Evaluate the untouched human gold set after choosing your training settings:

```bash
uv run --group training python -m training.evaluate_encoder checkpoints/poi-xlm-roberta-v1
# Add --cpu to evaluate without CUDA.
```

This writes `gold_metrics.json` without updating weights. Keep the original
labels file at its original path; evaluation verifies its checksum and split
manifest. Avoid repeatedly tuning against gold scores.

Run inference on a UTF-8 description file:

```bash
uv run --group training python -m training.predict_encoder \
  checkpoints/poi-xlm-roberta-v1 --text-file data/example-description.txt
```

Inference runs on CPU and prints `entities` with `name`, `category`, `start`,
and `end` character offsets. It refuses overlength inputs. The scraper is not
wired to this experimental model; integrating predictions is a separate step.

Convenience commands: `make training-fit`, `make training-evaluate`, and
`make training-predict TEXT_FILE=data/example-description.txt`. Override
`TRAINING_RUN` and `TRAINING_MODEL` to select a different output/checkpoint.
