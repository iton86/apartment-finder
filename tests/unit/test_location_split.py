"""
Tests for splitting imot.bg's location text into city / area / street.

The cases below cover the real source shapes: "град <city>, <area>", with
an optional street on a second line.
"""

import pytest

from apartment_finder.infrastructure.scrapers.imot_bg_scraper import ImotBgScraper

# (raw location text, expected city, expected area, expected street)
REAL_CASES = [
    ("град София, Манастирски ливади", "София", "Манастирски ливади", None),
    (
        "град София, Манастирски ливади\nул. Луи Айер",
        "София",
        "Манастирски ливади",
        "ул. Луи Айер",
    ),
    (
        "град София, Манастирски ливади\nбул. България",
        "София",
        "Манастирски ливади",
        "бул. България",
    ),
    ("град София, Лозенец", "София", "Лозенец", None),
    ("град София, Дружба 1\nул. 5049", "София", "Дружба 1", "ул. 5049"),
    (
        "град София, Манастирски ливади\nул. Акад. Георги Наджаков",
        "София",
        "Манастирски ливади",
        "ул. Акад. Георги Наджаков",
    ),
]


class TestParseLocation:
    @pytest.mark.parametrize(("raw", "city", "area", "street"), REAL_CASES)
    def test_real_values(self, raw, city, area, street):
        assert ImotBgScraper._parse_location(raw) == (city, area, street)

    def test_strips_the_city_word(self):
        # The column is already called city, so carrying "град" would be
        # redundant and would break grouping against any other source.
        assert ImotBgScraper._parse_location("град София, Център")[0] == "София"

    @pytest.mark.parametrize("prefix", ["град", "гр.", "село", "с."])
    def test_handles_the_other_settlement_prefixes(self, prefix):
        # Only "град" appears in the data so far, but imot.bg lists
        # villages too and an unstripped "с." would split the same place
        # into two distinct cities.
        city, area, _ = ImotBgScraper._parse_location(f"{prefix} Банкя, Центъра")
        assert (city, area) == ("Банкя", "Центъра")

    def test_keeps_the_street_type_prefix(self):
        # "ул. Мур" and a hypothetical "бул. Мур" are different places, so
        # the prefix is signal, not noise.
        assert ImotBgScraper._parse_location("град София, X\nбул. Мур")[2] == "бул. Мур"

    def test_splits_on_the_first_comma_only(self):
        # A neighbourhood containing a comma must survive whole rather
        # than being truncated at it.
        city, area, _ = ImotBgScraper._parse_location("град София, Изток, блок 5")
        assert (city, area) == ("София", "Изток, блок 5")

    def test_tolerates_surrounding_whitespace(self):
        assert ImotBgScraper._parse_location("  град София ,  Лозенец  \n  ул. Мур  ") == (
            "София",
            "Лозенец",
            "ул. Мур",
        )

    def test_ignores_blank_lines_between_the_two_parts(self):
        assert ImotBgScraper._parse_location("град София, Лозенец\n\nул. Мур") == (
            "София",
            "Лозенец",
            "ул. Мур",
        )

    def test_area_is_empty_when_there_is_no_comma(self):
        # Degenerate but must not raise: the city is still usable and the
        # scraper's empty-field warning is what surfaces it.
        assert ImotBgScraper._parse_location("град София") == ("София", "", None)

    @pytest.mark.parametrize("raw", ["", "   ", "\n", "\n\n"])
    def test_empty_input_yields_empty_fields(self, raw):
        assert ImotBgScraper._parse_location(raw) == ("", "", None)
