"""
Pure-function tests for the imot.bg adapter's parsing helpers. No browser
is launched here — these are the static methods that turn raw page text
and URLs into domain values, so they're testable in microseconds.

_extract_external_id gets the most attention because it produces the
dedup key: if it's wrong, listings either get re-saved forever or
collide and get silently skipped.
"""

import pytest

from apartment_finder.domain.entities import Currency, TransactionType
from apartment_finder.infrastructure.scrapers.imot_bg_scraper import ImotBgScraper

REAL_URL = (
    "https://www.imot.bg/obiava-1b178411519043054-prodava-dvustaen-apartament-grad-sofiya-oborishte"
)


class TestExtractExternalId:
    def test_keeps_alphanumeric_id_intact(self):
        # The "1b" prefix is part of the id. A digits-only regex drops it.
        assert ImotBgScraper._extract_external_id(REAL_URL) == "1b178411519043054"

    def test_ids_differing_only_in_prefix_do_not_collide(self):
        a = ImotBgScraper._extract_external_id("https://www.imot.bg/obiava-1b1784-prodava")
        b = ImotBgScraper._extract_external_id("https://www.imot.bg/obiava-1c1784-prodava")
        assert a != b

    def test_purely_numeric_id_still_works(self):
        url = "https://www.imot.bg/obiava-178411519043054-prodava"
        assert ImotBgScraper._extract_external_id(url) == "178411519043054"

    def test_falls_back_to_last_path_segment_when_pattern_absent(self):
        url = "https://www.imot.bg/some/other/page"
        assert ImotBgScraper._extract_external_id(url) == "page"


class TestParsePrice:
    def test_parses_euro(self):
        price = ImotBgScraper._parse_price("155 000 €")
        assert price.amount == 155000.0
        assert price.currency is Currency.EUR

    def test_defaults_to_bgn(self):
        price = ImotBgScraper._parse_price("303 000 лв.")
        assert price.amount == 303000.0
        assert price.currency is Currency.BGN

    @pytest.mark.parametrize("raw", ["", "по договаряне"])
    def test_returns_none_without_digits(self, raw):
        assert ImotBgScraper._parse_price(raw) is None


class TestParseTransactionType:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Обява: Продава 2-СТАЕН град София", TransactionType.SALE),
            ("Дава под наем 3-СТАЕН град София", TransactionType.RENT),
        ],
    )
    def test_parses_supported_transaction_types(self, title, expected):
        assert ImotBgScraper._parse_transaction_type(title) is expected

    def test_rejects_an_unknown_transaction_type(self):
        with pytest.raises(ValueError, match="Unsupported listing transaction type"):
            ImotBgScraper._parse_transaction_type("Заменя апартамент")


class TestParseRooms:
    def test_parses_rooms_from_listing_title(self):
        assert ImotBgScraper._parse_rooms("Продава 3-СТАЕН град София") == 3

    def test_returns_none_for_a_property_without_room_count(self):
        assert ImotBgScraper._parse_rooms("Продава ПАРЦЕЛ град София") is None


class TestParseBuildingFloors:
    @pytest.mark.parametrize(
        ("floor", "expected"),
        [("7-ми ет. от 8", 8), ("Партер от 4", 4), ("Мецанин", None), (None, None)],
    )
    def test_parses_total_floor_count(self, floor, expected):
        assert ImotBgScraper._parse_building_floors(floor) == expected


class TestParseFloor:
    @pytest.mark.parametrize(
        ("floor", "expected"),
        [("7-ми ет. от 8", 7), ("2-ри ет.", 2), ("Мецанин", None), (None, None)],
    )
    def test_returns_the_first_integer(self, floor, expected):
        assert ImotBgScraper._parse_floor(floor) == expected


class TestNormalizePhone:
    def test_removes_all_whitespace(self):
        assert ImotBgScraper._normalize_phone("+359 888\u00a0123\n456") == "+359888123456"

    def test_returns_none_for_an_empty_value(self):
        assert ImotBgScraper._normalize_phone("   ") is None


class TestChromiumArgs:
    def test_no_extra_args_outside_a_container(self, monkeypatch):
        # --no-sandbox drops a real security boundary, so a plain local run
        # must not silently get it.
        monkeypatch.delenv("CHROMIUM_IN_CONTAINER", raising=False)
        assert ImotBgScraper._chromium_args() == []

    def test_container_flag_enables_sandbox_and_shm_workarounds(self, monkeypatch):
        monkeypatch.setenv("CHROMIUM_IN_CONTAINER", "1")
        assert ImotBgScraper._chromium_args() == ["--no-sandbox", "--disable-dev-shm-usage"]

    def test_ignores_values_other_than_1(self, monkeypatch):
        monkeypatch.setenv("CHROMIUM_IN_CONTAINER", "0")
        assert ImotBgScraper._chromium_args() == []


class TestResolveImageUrls:
    def test_resolves_protocol_relative_src_against_base_url(self):
        urls = ImotBgScraper._resolve_image_urls(["//x.imot.bg/photo/1.jpg"])
        assert urls == ["https://x.imot.bg/photo/1.jpg"]

    def test_skips_missing_srcs(self):
        # get_attribute returns None for both data-src and src on a slide
        # that carries neither.
        assert ImotBgScraper._resolve_image_urls([None, "//x.imot.bg/1.jpg", None]) == [
            "https://x.imot.bg/1.jpg"
        ]

    def test_skips_inline_placeholder(self):
        placeholder = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="
        assert ImotBgScraper._resolve_image_urls([placeholder]) == []

    def test_dedups_cloned_slides_keeping_first_seen_order(self):
        # Owl's cloned slides and the thumbnail strip repeat the same photos;
        # sequence numbers must follow the order the ad shows them in.
        urls = ImotBgScraper._resolve_image_urls(
            ["//x.imot.bg/1.jpg", "//x.imot.bg/2.jpg", "//x.imot.bg/1.jpg"]
        )
        assert urls == ["https://x.imot.bg/1.jpg", "https://x.imot.bg/2.jpg"]

    def test_returns_empty_list_for_a_gallery_with_no_photos(self):
        assert ImotBgScraper._resolve_image_urls([]) == []


class TestParseAdParams:
    # What page.locator("div.adParams > div").all_inner_texts() returns for
    # the real block: the <br> renders as a newline, <sup>2</sup> as a bare
    # "2", and the "Строителство" row's three text nodes run together.
    REAL_ROWS = [
        "Площ:\n55 m2",
        "Етаж:\nПартер от 4",
        "Строителство:\nТухла, Въведен в експлоатация 2013 г.",
    ]

    def test_keys_every_row_by_its_label(self):
        params = ImotBgScraper._parse_ad_params(self.REAL_ROWS)
        assert params == {
            "Площ": "55 m2",
            "Етаж": "Партер от 4",
            "Строителство": "Тухла, Въведен в експлоатация 2013 г.",
        }

    def test_area_row_parses_to_a_number_ignoring_the_sup_2(self):
        params = ImotBgScraper._parse_ad_params(self.REAL_ROWS)
        assert ImotBgScraper._parse_number(params["Площ"]) == 55.0

    def test_missing_row_leaves_other_labels_intact(self):
        # A plot of land has no "Етаж" row. Position-based parsing would
        # shift "Строителство" into the floor field here.
        params = ImotBgScraper._parse_ad_params(["Площ:\n420 m2", "Строителство:\nТухла"])
        assert params["Площ"] == "420 m2"
        assert "Етаж" not in params

    def test_survives_markup_without_the_br(self):
        assert ImotBgScraper._parse_ad_params(["Площ:55 m2"]) == {"Площ": "55 m2"}

    def test_first_occurrence_of_a_label_wins(self):
        params = ImotBgScraper._parse_ad_params(["Площ:\n55 m2", "Площ:\n99 m2"])
        assert params["Площ"] == "55 m2"

    @pytest.mark.parametrize("rows", [[], [""], ["no colon here"]])
    def test_returns_empty_dict_when_there_is_nothing_to_key(self, rows):
        assert ImotBgScraper._parse_ad_params(rows) == {}


class TestParseNumber:
    def test_parses_area(self):
        assert ImotBgScraper._parse_number("75 кв.м") == 75.0

    def test_treats_comma_as_decimal_separator(self):
        assert ImotBgScraper._parse_number("75,5 кв.м") == 75.5

    @pytest.mark.parametrize("raw", ["", "n/a"])
    def test_returns_none_when_unparseable(self, raw):
        assert ImotBgScraper._parse_number(raw) is None
