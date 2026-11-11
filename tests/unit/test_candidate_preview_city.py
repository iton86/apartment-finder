"""Regression checks for the read-only preview's geographic guardrails."""

from apartment_finder.infrastructure.geo.candidate_preview import (
    _inside_sofia_preview,
    _sofia_city,
)


def test_preview_accepts_sofia_city_variants():
    assert _sofia_city("гр. София")
    assert _sofia_city("Sofia")
    assert not _sofia_city("Пловдив")
    assert not _sofia_city("")


def test_preview_rejects_distant_candidate_markers():
    assert _inside_sofia_preview([23.32, 42.70])
    assert not _inside_sofia_preview([24.75, 42.14])  # Plovdiv
    assert not _inside_sofia_preview([27.47, 42.50])  # Burgas
