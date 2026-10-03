"""
Tests for the description cleaner used before POI labeling and inference.

The removal cases are the contact-noise shapes seen in real imot.bg ads;
the keep cases guard numbers that look phone-ish but carry meaning
(prices, years, distances) and must survive, or labels silently vanish.
"""

import pytest

from training.text_cleaning import clean_description


class TestRemovesContactNoise:
    @pytest.mark.parametrize(
        "phone",
        ["0887-88-99-98", "0888 123 456", "+359 88 123 4567", "02/123 45 67", "0898123456"],
    )
    def test_phones(self, phone):
        assert clean_description(f"Обадете се на {phone} за оглед") == "Обадете се на за оглед"

    def test_email_and_url(self):
        text = "Пишете на office@titan-bg.com или вижте https://example.bg/oferta?id=1 и www.x.bg"
        assert clean_description(text) == "Пишете на или вижте и"

    def test_reference_number(self):
        assert clean_description("Хубав имот.\nРеф. номер: 2116090.") == "Хубав имот."

    def test_drops_line_left_with_only_an_emoji(self):
        assert clean_description("За оглед:\n📞 0887-88-99-98") == "За оглед:"


class TestKeepsMeaningfulText:
    @pytest.mark.parametrize(
        "text",
        [
            "229 000 € за 63 кв.м",
            "Акт 16 от 2008 г.",
            "на 300 м от метростанция Джеймс Баучер",
            "10 мин от МОЛ БЪЛГАРИЯ",
            "✓ Отлична локация до ЮЖЕН ПАРК!",
            "Fully refurbished apartment",
        ],
    )
    def test_unchanged(self, text):
        assert clean_description(text) == text


class TestWhitespace:
    def test_collapses_blank_lines_but_keeps_paragraphs(self):
        assert clean_description("a\r\n\r\n\r\n\r\nb  \t c d") == "a\n\nb c d"

    def test_strips_invisible_characters(self):
        assert clean_description("пар​к﻿") == "парк"

    @pytest.mark.parametrize("empty", [None, "", "   \n  "])
    def test_empty(self, empty):
        assert clean_description(empty) == ""
