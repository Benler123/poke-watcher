"""Decide whether an eBay listing is really the product a watch tracks.

eBay keyword search is fuzzy: a search for an English Umbreon VMAX also returns
Japanese printings, proxies, lots and unrelated cards that happen to share a
word. These checks run on the listing title before the price rules.
"""

import re
from collections.abc import Mapping
from typing import Any

# The tcgcsv index is TCGplayer's English Pokemon category, so a listing that
# advertises another language is a different product.
FOREIGN = re.compile(
    r"\b(?:japanese|japan|jpn|jap|korean|korea|chinese|china|s-chinese|t-chinese|"
    r"german|deutsch|french|francais|spanish|espanol|italian|italiano|portuguese|"
    r"russian|thai|indonesian)\b",
    re.IGNORECASE,
)

# Listings that are not a single copy of the card, or not a real card at all.
JUNK = re.compile(
    r"\b(?:lot|lots|bundle|bulk|proxy|proxies|custom|fake|replica|orica|"
    r"digital|online\s*code|code\s*card|repack|mystery|you\s*pick|choose\s*your|"
    r"read\s*description|not\s*real)\b",
    re.IGNORECASE,
)

# Words in set names that carry no identifying weight in an eBay title.
SET_STOPWORDS = frozenset({"pokemon", "tcg", "the", "and", "of", "series"})

# tcgcsv set names are prefixed with TCGplayer's set code ("SWSH07: Evolving
# Skies"), which never appears in an eBay title.
_SET_CODE_PREFIX = re.compile(r"^[a-z0-9&\s.]{1,8}:\s*", re.IGNORECASE)
_SET_CODE_TOKEN = re.compile(r"^(?=.*[a-z])(?=.*\d)[a-z\d]+$", re.IGNORECASE)

_TOKEN = re.compile(r"[a-z0-9]+")
_PARENTHETICAL = re.compile(r"\([^)]*\)")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _contains_token(title_tokens: set[str], token: str) -> bool:
    return token in title_tokens


def _name_matches(title_tokens: set[str], name: str) -> bool:
    """Every word of the card name (minus parentheticals) appears in the title."""
    base = _PARENTHETICAL.sub(" ", name)
    wanted = [token for token in _tokens(base) if len(token) > 1 or token.isdigit()]
    return bool(wanted) and all(_contains_token(title_tokens, token) for token in wanted)


def _number_matches(title: str, number: str) -> bool:
    """A collector number such as ``215/203``, ``SWSH284`` or ``TG20/TG30``."""
    number = number.strip().lower()
    if not number:
        return False
    lowered = title.lower()
    head = number.split("/")[0]
    patterns = [
        rf"(?<![a-z0-9]){re.escape(head)}\s*/\s*[a-z0-9]+",  # 215/203
        rf"#\s*{re.escape(head)}(?![a-z0-9])",  # #215
    ]
    if not head.isdigit():
        patterns.append(rf"(?<![a-z0-9]){re.escape(head)}(?![a-z0-9])")  # SWSH284
    return any(re.search(pattern, lowered) for pattern in patterns)


def _set_matches(title_tokens: set[str], group_name: str) -> bool:
    """A distinctive word of the set name appears, e.g. "Evolving Skies"."""
    wanted = [
        token
        for token in _tokens(_SET_CODE_PREFIX.sub("", group_name))
        if len(token) > 2
        and token not in SET_STOPWORDS
        and not _SET_CODE_TOKEN.match(token)
    ]
    return bool(wanted) and all(_contains_token(title_tokens, token) for token in wanted)


def _variant_matches(title_tokens: set[str], name: str) -> bool:
    """The parenthetical variant, e.g. "(Alternate Art Secret)"."""
    wanted = [
        token
        for match in _PARENTHETICAL.findall(name)
        for token in _tokens(match)
        if len(token) > 2
    ]
    return bool(wanted) and all(_contains_token(title_tokens, token) for token in wanted)


def reject_reason(title: str, product: Mapping[str, Any] | None) -> str | None:
    """Why this title is not the watched product, or None when it looks right."""
    if FOREIGN.search(title):
        return "non-English listing"
    if JUNK.search(title):
        return "lot, proxy or non-card listing"
    if product is None:
        return None

    title_tokens = set(_tokens(title))
    name = product.get("name") or product.get("clean_name") or ""
    if name and not _name_matches(title_tokens, name):
        return "card name missing from title"

    # A bare name is ambiguous across sets and reprints, so a single card also
    # has to carry its collector number, set or variant. Sealed product names
    # ("Prismatic Evolutions Elite Trainer Box") are specific on their own.
    if product.get("sealed"):
        return None
    number = product.get("number") or ""
    group_name = product.get("group_name") or ""
    corroborated = (
        _number_matches(title, number)
        or _set_matches(title_tokens, group_name)
        or _variant_matches(title_tokens, name)
    )
    return None if corroborated else "no collector number or set in title"


def matches(title: str, product: Mapping[str, Any] | None) -> bool:
    return reject_reason(title, product) is None
