"""JSON Lines helpers shared by the pipeline steps. UTF-8 throughout, and
ensure_ascii=False so Cyrillic stays readable when you open the files."""

import json
from collections.abc import Iterable
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    """Read a JSON Lines file into a list of dicts, skipping blank lines.

    A missing file is treated as empty rather than an error.
    """
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, records: Iterable[dict], append: bool = False) -> None:
    """Write ``records`` as one JSON object per line, creating parent directories.

    Args:
        path: Destination file; a plain string is accepted and converted.
        records: Dicts to serialize.
        append: Append to the file instead of overwriting it.
    """
    if not isinstance(path, Path):
        path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if append else "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
