"""Decode BIO sequences independently of training dependencies."""


def entities(tags: list[str]) -> set[tuple[int, int, str]]:
    """Decode BIO spans; treat orphan I tags as a new entity for scoring."""
    result = set()
    start, category = None, None
    for i, tag in enumerate([*tags, "O"]):
        prefix, _, current = tag.partition("-")
        if start is not None and (prefix != "I" or current != category):
            result.add((start, i, category))
            start, category = None, None
        if prefix in {"B", "I"} and start is None:
            start, category = i, current
    return result
