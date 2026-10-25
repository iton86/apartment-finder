import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from training.dedup import cluster_near_duplicates
from training.jsonl import read_jsonl, write_jsonl
from training.prepare_dataset import build_snapshot, poi_types, prepare
from training.token_data import split_rows


def fixtures():
    texts = [
        "Слънчев апартамент до ул. Шишман с просторна кухня хол балкон асансьор гараж",
        "Модерно жилище близо до Женски пазар предлага отлична гледка север юг тераса",
        "Тристаен до Южен парк ремонтиран обзаведен климатик спалня мазе склад градина",
        "Бизнес офис светъл първи етаж комуникативен район с големи прозорци",
        "Къща двор овощни дървета простор веранда тухлена постройка далеч от шума",
        "Студио отдава дългосрочно нови мебели отделен вход поддържана сграда",
        "Луксозен мезонет покривна тераса камина панорама планина самостоятелен асансьор",
        "Партер работилница просторна зала санитарен възел товарен вход индустриална зона",
    ]
    pois = [
        [{"category": "street", "name": "ул. Шишман", "evidence": "до ул. Шишман"}],
        [{"category": "market", "name": "Женски пазар", "evidence": "близо до Женски пазар"}],
        [{"category": "park", "name": "Южен парк", "evidence": "до Южен парк"}],
    ]
    rows = []
    for i, text in enumerate(texts):
        entries = copy.deepcopy(pois[i]) if i < len(pois) else []
        for poi in entries:
            poi["distance"] = {"value": None, "unit": "nearby"}
        rows.append({
            "id": str(i), "content_hash": hashlib.sha256(text.encode()).hexdigest(),
            "description": text, "pois": entries, "label_source": "human",
            "approved_revision": 199174988752800, "city": "София",
            "area": "Център" if i % 2 else "Лозенец",
        })
    return rows


class DatasetPreparationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "approved.jsonl"
        self.rows = fixtures()
        write_jsonl(self.source, self.rows)

    def snapshot(self):
        return build_snapshot(self.source, self.root / "v1", gold_version="gold-v1")

    def test_export_has_hashes_provenance_and_reproducible_training_splits(self):
        manifest = self.snapshot()
        for name, checksum in manifest["files"].items():
            self.assertEqual(
                hashlib.sha256((self.root / "v1" / name).read_bytes()).hexdigest(), checksum
            )
        actual = split_rows(read_jsonl(self.root / "v1/labels.jsonl"), 0.15, 42)
        for split, rows in actual.items():
            self.assertEqual(rows, read_jsonl(self.root / "v1" / f"{split}.jsonl"))
            self.assertEqual(
                [r["content_hash"] for r in rows],
                [r["content_hash"] for r in manifest["splits"][split]],
            )
        self.assertEqual(manifest["gold_version"], "gold-v1")
        self.assertEqual(manifest["splits"]["gold"][0]["approved_revision"], 199174988752800)
        self.assertIn("category:street", set().union(*(
            set(report["features"]) for report in manifest["coverage"].values()
        )))

    def test_reordering_input_does_not_change_membership(self):
        labels, splits, _ = prepare(self.rows)
        other_labels, other_splits, _ = prepare(list(reversed(self.rows)))
        self.assertEqual(labels, other_labels)
        self.assertEqual(splits, other_splits)

    def test_absolute_count_balances_types_and_is_order_independent(self):
        # Many independent street ads must not drown out the other types.
        rows = copy.deepcopy(self.rows)
        for i in range(10):
            row = copy.deepcopy(self.rows[0])
            row["description"] = f"Unique{i} different{i} apartment{i} ул. Шишман"
            row["pois"][0]["evidence"] = "ул. Шишман"
            row["content_hash"] = hashlib.sha256(row["description"].encode()).hexdigest()
            row["area"] = f"Area{i}"
            rows.append(row)
        labels, splits, _ = prepare(rows, gold_count=4)
        self.assertEqual(len(splits["gold"]), 4)
        self.assertEqual(
            set().union(*(poi_types(row) for row in splits["gold"])),
            {"street", "market", "park", "no_pois"},
        )
        other_labels, other_splits, _ = prepare(list(reversed(rows)), gold_count=4)
        self.assertEqual(labels, other_labels)
        self.assertEqual(splits, other_splits)

    def test_exact_count_excludes_duplicate_neighbors_and_preserves_training(self):
        rows = copy.deepcopy(self.rows[:3])
        for i in range(6):
            row = {**self.rows[0], "content_hash": f"duplicate-{i}"}
            rows.append(row)
        _, splits, _ = prepare(rows, gold_count=7)
        self.assertEqual(len(splits["gold"]), 7)
        self.assertTrue(all(row["pois"][0]["category"] == "street" for row in splits["gold"]))
        self.assertEqual(len(splits["train"]), 1)
        self.assertEqual(len(splits["validation"]), 1)
        _, splits, _ = prepare(rows, gold_count=1)
        self.assertEqual(len(splits["gold"]), 1)
        clusters = cluster_near_duplicates([
            row["description"] for row in splits["gold"] + splits["train"] + splits["validation"]
        ])
        self.assertNotIn(clusters[0], clusters[1:])

    def test_invalid_or_impossible_absolute_count_is_rejected(self):
        for count in (0, -1, 1.5, True, len(self.rows) - 1, len(self.rows) + 1):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, "gold-count"):
                prepare(self.rows, gold_count=count)

    def test_update_rebalances_to_total_count_and_requires_new_version(self):
        self.snapshot()
        previous = self.root / "v1/manifest.json"
        for version in (None, "gold-v1"):
            with self.assertRaisesRegex(ValueError, "new --gold-version"):
                build_snapshot(
                    self.source, self.root / "v2", gold_manifest=previous,
                    gold_count=4, gold_version=version,
                )
            self.assertFalse((self.root / "v2").exists())
        manifest = build_snapshot(
            self.source, self.root / "v2", gold_manifest=previous,
            gold_count=4, gold_version="gold-v2",
        )
        self.assertEqual(manifest["gold_version"], "gold-v2")
        self.assertEqual(manifest["gold_count"], 4)
        self.assertIsNone(manifest["gold_fraction"])
        self.assertEqual(manifest["gold_selection"], "balanced_by_poi_type")
        self.assertEqual(len(manifest["gold_rows"]), 4)
        self.assertEqual(len(read_jsonl(self.root / "v2/gold.jsonl")), 4)

    def test_count_overrides_fraction_and_multilabel_types_count_once(self):
        _, splits, _ = prepare(self.rows, gold_count=2, gold_fraction=0)
        self.assertEqual(len(splits["gold"]), 2)
        row = copy.deepcopy(self.rows[0])
        row["pois"] *= 2
        row["pois"].extend(copy.deepcopy(self.rows[1]["pois"]))
        self.assertEqual(poi_types(row), {"street", "market"})

    def test_frozen_gold_is_reused_without_adding_new_neighbors(self):
        manifest = self.snapshot()
        key = manifest["gold_rows"][0]["content_hash"]
        member = next(r for r in self.rows if r["content_hash"] == key)
        neighbor = {**member, "id": "new",
                    "description": member["description"] + " !!!"}
        neighbor["content_hash"] = hashlib.sha256(neighbor["description"].encode()).hexdigest()
        _labels, splits, version = prepare(self.rows + [neighbor], frozen=manifest)
        self.assertEqual(version, "gold-v1")
        self.assertEqual(
            {r["content_hash"] for r in splits["gold"]},
            {r["content_hash"] for r in manifest["gold_rows"]},
        )
        self.assertIn(neighbor["content_hash"], {r["content_hash"] for r in splits["excluded"]})
        all_rows = [r for rows in splits.values() for r in rows]
        clusters = cluster_near_duplicates([r["description"] for r in all_rows])
        assignments = {}
        offset = 0
        for split, rows in splits.items():
            assignments[split] = set(clusters[offset:offset + len(rows)])
            offset += len(rows)
        self.assertFalse(assignments["gold"] & assignments["train"])
        self.assertFalse(assignments["gold"] & assignments["validation"])
        self.assertFalse(assignments["train"] & assignments["validation"])

    def test_changed_or_missing_frozen_gold_is_rejected(self):
        manifest = self.snapshot()
        key = manifest["gold_rows"][0]["content_hash"]
        with self.assertRaisesRegex(ValueError, "missing"):
            prepare([r for r in self.rows if r["content_hash"] != key], frozen=manifest)
        edited = copy.deepcopy(self.rows)
        member = next(r for r in edited if r["content_hash"] == key)
        member["pois"] = [] if member["pois"] else [{
            "category": "other", "name": member["description"].split()[0],
            "distance": {"value": None, "unit": "nearby"},
            "evidence": member["description"],
        }]
        with self.assertRaisesRegex(ValueError, "changed"):
            prepare(edited, frozen=manifest)

    def test_existing_snapshot_is_never_overwritten(self):
        self.snapshot()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.snapshot()

    def test_empty_small_duplicate_or_teacher_input_is_rejected(self):
        for rows in ([], self.rows[:2], self.rows + [self.rows[0]],
                     [{**r, "label_source": "teacher"} for r in self.rows]):
            with self.subTest(rows=len(rows)), self.assertRaises(ValueError):
                prepare(rows)

    def test_snapshot_reuses_manifest(self):
        self.snapshot()
        manifest = build_snapshot(
            self.source, self.root / "v2", gold_manifest=self.root / "v1/manifest.json"
        )
        self.assertEqual(manifest["gold_version"], "gold-v1")
        self.assertEqual(manifest["gold_selection"], "frozen")
        self.assertEqual(manifest["dataset_version"], "v2")
        json.loads((self.root / "v2/manifest.json").read_text(encoding="utf-8"))

    def test_cli_creates_snapshot_and_reports_training_command(self):
        result = subprocess.run(
            [
                sys.executable, "-m", "training.prepare_dataset",
                "--labels", str(self.source), "--output", str(self.root / "cli-v1"),
                "--gold-version", "gold-v1",
                "--gold-count", "4",
            ],
            capture_output=True, text=True, encoding="utf-8", check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "cli-v1/manifest.json").exists())
        self.assertEqual(len(read_jsonl(self.root / "cli-v1/gold.jsonl")), 4)
        self.assertIn("training.finetune", result.stdout)


if __name__ == "__main__":
    unittest.main()
