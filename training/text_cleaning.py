"""
Normalizes a listing description before it is labeled or fed to the model.

The same function MUST run at inference time too: a model fine-tuned on
phone-free, whitespace-normalized text sees a slightly different input
distribution if production feeds it raw ads. Keep this module free of
third-party imports so the future inference adapter can reuse it as-is.

What gets removed is contact noise only — phones, e-mails, URLs, agency
reference numbers. Anything that could describe a location (street names,
"10 мин от МОЛ България", "✓ Отлична локация") is left untouched, because
deleting it would silently delete labels.
"""

import re
import unicodedata

# Bulgarian numbers as they appear in ads: 0887-88-99-98, 0888 123 456,
# +359 88 123 4567, 02/123 45 67. The leading 0 / +359 plus 8–9 further
# digits is what keeps prices ("229 000"), years ("2008 г.") and agency
# reference numbers ("2116090") from matching.
_PHONE = re.compile(r"(?<![\d+])(?:\+359|00359|0)(?:[\s\-./]?\d){8,9}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_REFERENCE = re.compile(
    r"\b(?:реф(?:ерентен)?\.?\s*(?:номер|№)|ref\.?\s*(?:no\.?|№))\s*[:.]?\s*\S+", re.I
)

# Invisible characters that survive copy-paste from Word/agency CRMs and
# would otherwise become stray tokens.
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)

# After stripping a phone, "📞 0887-88-99-98" leaves just "📞". A line with
# no letter or digit left carries no information for POI extraction.
_HAS_WORD_CHAR = re.compile(r"[^\W_]")


def clean_description(text: str | None) -> str:
    if not text:
        return ""

    text = unicodedata.normalize("NFC", text).translate(_INVISIBLE)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")

    for pattern in (_URL, _EMAIL, _PHONE, _REFERENCE):
        text = pattern.sub("", text)

    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line and not _HAS_WORD_CHAR.search(line):
            continue
        lines.append(line)

    # Keep paragraph breaks (they separate the "В близост до имота:" block
    # from the rest) but collapse runs of blank lines into one.
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
