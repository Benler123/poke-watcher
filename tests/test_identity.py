"""Listing-to-product verification."""

import pytest

from app import identity

UMBREON = {
    "name": "Umbreon VMAX (Alternate Art Secret)",
    "clean_name": "Umbreon VMAX Alternate Art Secret",
    "group_name": "SWSH07: Evolving Skies",
    "number": "215/203",
    "sealed": False,
}

CHARIZARD = {
    "name": "Charizard ex",
    "clean_name": "Charizard ex",
    "group_name": "SV03: Obsidian Flames",
    "number": "125/197",
    "sealed": False,
}

ETB = {
    "name": "Prismatic Evolutions Elite Trainer Box",
    "clean_name": "Prismatic Evolutions Elite Trainer Box",
    "group_name": "SV: Prismatic Evolutions",
    "number": None,
    "sealed": True,
}


@pytest.mark.parametrize(
    "title",
    [
        "Pokemon Umbreon VMAX Alt Art 215/203 Evolving Skies NM",
        "Umbreon VMAX #215 Alternate Art Secret Rare Evolving Skies",
        "PSA 10 Umbreon VMAX Alternate Art Secret Evolving Skies 215/203",
    ],
)
def test_accepts_the_watched_card(title):
    assert identity.matches(title, UMBREON)


@pytest.mark.parametrize(
    "title",
    [
        "Japanese Umbreon VMAX Alt Art 215/203 Eevee Heroes",
        "Umbreon VMAX 215/203 Custom Proxy Card",
        "Pokemon Card Lot Umbreon VMAX 215/203 and more",
        "Espeon VMAX Alternate Art 215/203 Evolving Skies",
        "Umbreon VMAX Pokemon Card NM",
    ],
)
def test_rejects_everything_else(title):
    assert not identity.matches(title, UMBREON)


def test_number_or_set_is_enough():
    assert identity.matches("Charizard ex 125/197 Pokemon", CHARIZARD)
    assert identity.matches("Charizard ex Obsidian Flames Holo Rare", CHARIZARD)
    # A Charizard ex exists in several sets, so neither is a match on its own.
    assert not identity.matches("Charizard ex Ultra Rare Pokemon Card", CHARIZARD)


def test_sealed_needs_no_collector_number():
    assert identity.matches("Pokemon Prismatic Evolutions Elite Trainer Box Sealed", ETB)
    assert not identity.matches("Prismatic Evolutions Booster Bundle", ETB)


def test_language_and_junk_apply_without_a_product():
    assert not identity.matches("Japanese Pokemon card", None)
    assert not identity.matches("Pokemon repack mystery box", None)
    assert identity.matches("Pikachu Promo Near Mint", None)
