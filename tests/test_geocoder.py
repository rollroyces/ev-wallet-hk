"""Tests for the EPD-station geocoder (English-name extraction only —
the live HTTP loop is verified manually in docs/operations/GEOCODER.md)."""

from __future__ import annotations

import pytest

from evwallet.internal.geocoder import (
    _district_to_english,
    _extract_english_name,
    _simplify_name,
)

# ---------------------------------------------------------------------------
# _extract_english_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Bilingual: English first, Chinese trailing
        ("The Centrium 中央廣場", "The Centrium"),
        ("Queensway Government Offices 金鐘道政府合署", "Queensway Government Offices"),
        ("Hong Kong Island 香港島", "Hong Kong Island"),
        (
            "One International Finance Centre 國際金融中心一期",
            "One International Finance Centre",
        ),
        (
            "TWO IFC (International Finance Centre) - IFC II 國際 金融中心二期",
            "TWO IFC (International Finance Centre) - IFC II",
        ),
        # All-English: passthrough
        ("Some Pure English Name", "Some Pure English Name"),
        # Edge cases
        ("", ""),
        ("純中文", ""),  # no English part
        ("  spaces  around  ", "spaces around"),
        # Single Chinese char sandwiched: not the typical case but make
        # sure it doesn't crash
        ("Building A 廈", "Building A"),
    ],
)
def test_extract_english_name(raw: str, expected: str) -> None:
    assert _extract_english_name(raw) == expected


# ---------------------------------------------------------------------------
# _simplify_name
# ---------------------------------------------------------------------------


def test_simplify_name_drops_parentheticals() -> None:
    """Parenthesised qualifiers like '(International Finance Centre)'
    are stripped — they confuse Nominatim."""
    cands = _simplify_name("TWO IFC (International Finance Centre) - IFC II")
    # First is full
    assert cands[0] == "TWO IFC (International Finance Centre) - IFC II"
    # Second drops the parenthetical
    assert any("(" not in c and c == "TWO IFC - IFC II" for c in cands[1:])


def test_simplify_name_chunks_on_dash() -> None:
    """Trailing ' - QUALIFIER' is dropped."""
    cands = _simplify_name("Foo Building - Annex Block B")
    # "Foo Building" should appear as a candidate
    assert "Foo Building" in cands


def test_simplify_name_short_input() -> None:
    cands = _simplify_name("Cheung Kong Center")
    assert "Cheung Kong Center" in cands


def test_simplify_name_empty_input() -> None:
    assert _simplify_name("") == []


# ---------------------------------------------------------------------------
# _district_to_english
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Multi-letter codes
        ("C & W 中西區", "Central and Western"),
        ("Wan C 灣仔", "Wan Chai"),
        ("E 東區", "Eastern"),
        ("S 南區", "Southern"),
        ("Yau T M 油尖旺", "Yau Tsim Mong"),
        ("Kln C 九龍城", "Kowloon City"),
        ("W Ts 黃大仙", "Wong Tai Sin"),
        ("Kwn T 觀塘", "Kwun Tong"),
        ("T W 荃灣", "Tsuen Wan"),
        ("T P 屯門", "Tuen Mun"),
        ("Y L 元朗", "Yuen Long"),
        ("N T 北區", "North"),
        ("T M W 將軍澳", "Tseung Kwan O"),
        ("S K 西貢", "Sai Kung"),
        ("Is 離島", "Islands"),
        # Edge cases
        ("", ""),
        (None, ""),
        ("Unknown District", ""),  # unknown code -> empty string
    ],
)
def test_district_to_english(raw: str | None, expected: str) -> None:
    assert _district_to_english(raw) == expected
