"""Grade handling: build eBay queries and match listing titles for graded cards."""

import re
from collections.abc import Mapping
from typing import Any

GRADERS = ("PSA", "BGS", "CGC", "SGC", "ACE", "TAG")
RAW = "RAW"

_ANY_GRADE = re.compile(
    rf"\b(?:{'|'.join(GRADERS)})\s*[-:]?\s*(?:10|9\.5|[1-9](?:\.5)?)\b",
    re.IGNORECASE,
)
_RAW_HINTS = re.compile(r"\b(?:graded|slab(?:bed)?|gem\s*mint\s*10)\b", re.IGNORECASE)


def normalize_company(company: str | None) -> str:
    value = (company or "").strip().upper()
    if value in {"", "ANY"}:
        return ""
    if value == RAW or value in {"RAW", "UNGRADED"}:
        return RAW
    return value if value in GRADERS else ""


def normalize_value(value: str | float | None) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    try:
        number = float(text)
    except ValueError:
        return ""
    if not 1 <= number <= 10:
        return ""
    return str(int(number)) if number.is_integer() else f"{number:g}"


def describe(company: str | None, value: str | float | None) -> str:
    company = normalize_company(company)
    grade = normalize_value(value)
    if company == RAW:
        return "Raw (ungraded)"
    if company and grade:
        return f"{company} {grade}"
    if company:
        return company
    if grade:
        return f"Grade {grade}"
    return ""


def search_query(watch: Mapping[str, Any]) -> str:
    """The watch's eBay query with grade terms appended."""
    query = (watch.get("ebay_query") or "").strip()
    company = normalize_company(watch.get("grade_company"))
    grade = normalize_value(watch.get("grade_value"))
    if company == RAW:
        return query
    extra = " ".join(part for part in (company, grade) if part)
    return f"{query} {extra}".strip() if extra else query


def _grade_pattern(company: str, grade: str) -> re.Pattern[str]:
    graders = company or "|".join(GRADERS)
    number = re.escape(grade) if grade else r"10|9\.5|[1-9](?:\.5)?"
    return re.compile(rf"\b(?:{graders})\s*[-:]?\s*(?:{number})\b", re.IGNORECASE)


def matches(title: str, watch: Mapping[str, Any]) -> bool:
    """Whether a listing title carries the grade the watch asked for.

    eBay's keyword search is fuzzy, so a PSA 10 query still returns raw cards and
    other grades; this is the filter that keeps them out.
    """
    company = normalize_company(watch.get("grade_company"))
    grade = normalize_value(watch.get("grade_value"))
    if not company and not grade:
        return True
    if company == RAW:
        return not _ANY_GRADE.search(title) and not _RAW_HINTS.search(title)
    return bool(_grade_pattern(company, grade).search(title))
