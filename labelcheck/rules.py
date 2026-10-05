"""Deterministic comparison rules: application fields vs. a label reading.

No AI here. Every verdict comes from parsing, normalizing and comparing
plain strings/numbers.
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

from labelcheck.models import ApplicationFields, FieldResult, LabelReading, Verdict

GOVERNMENT_WARNING_TEXT = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not "
    "drink alcoholic beverages during pregnancy because of the risk of birth "
    "defects. (2) Consumption of alcoholic beverages impairs your ability to "
    "drive a car or operate machinery, and may cause health problems."
)

GOVERNMENT_WARNING_HEADING = "GOVERNMENT WARNING:"

_FUZZY_REVIEW_THRESHOLD = 92.0


# ---------------------------------------------------------------------------
# Text normalization helpers
# ---------------------------------------------------------------------------

_QUOTE_MAP = {
    "‘": "'",  # left single quote
    "’": "'",  # right single quote (curly apostrophe)
    "“": '"',  # left double quote
    "”": '"',  # right double quote
    "′": "'",
    "″": '"',
}


def _unify_quotes(text: str) -> str:
    for curly, straight in _QUOTE_MAP.items():
        text = text.replace(curly, straight)
    return text


def normalize_text(text: str) -> str:
    """Casefold, unify quotes, collapse whitespace, strip meaning-neutral punctuation."""
    text = unicodedata.normalize("NFKC", text)
    text = _unify_quotes(text)
    text = text.casefold()
    # Drop punctuation that doesn't change meaning (periods, commas, etc.)
    # but keep apostrophes and alphanumerics so "Stone's" stays "stone's".
    text = re.sub(r"[^\w\s']", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Alcohol content parsing
# ---------------------------------------------------------------------------


def parse_abv(text: str) -> float | None:
    """Pull the ABV percentage out of a free-form alcohol statement."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if match:
        return float(match.group(1))
    # Fallback: "Alc. 45 by Vol" style without a percent sign.
    match = re.search(r"alc\w*\.?\s*(\d+(?:\.\d+)?)\s*(?:%|by\s*vol)", text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def parse_proof(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*proof", text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


# ---------------------------------------------------------------------------
# Net contents parsing
# ---------------------------------------------------------------------------

_ML_PER_NORMALIZED_UNIT = {
    "ml": 1.0,
    "cl": 10.0,
    "l": 1000.0,
    "floz": 29.5735,
}


def parse_net_contents_ml(text: str) -> float | None:
    """Parse a net-contents string like '750 mL', '0.75 L', '25.4 fl oz' into mL."""
    cleaned = text.strip().lower()
    # fl oz variants: "fl oz", "fl. oz.", "floz"
    match = re.search(r"(\d+(?:\.\d+)?)\s*fl\.?\s*oz\.?", cleaned)
    if match:
        value = float(match.group(1))
        return value * _ML_PER_NORMALIZED_UNIT["floz"]

    match = re.search(r"(\d+(?:\.\d+)?)\s*(ml|milliliters?|millilitres?)\b", cleaned)
    if match:
        return float(match.group(1)) * _ML_PER_NORMALIZED_UNIT["ml"]

    match = re.search(r"(\d+(?:\.\d+)?)\s*(cl|centiliters?|centilitres?)\b", cleaned)
    if match:
        return float(match.group(1)) * _ML_PER_NORMALIZED_UNIT["cl"]

    match = re.search(r"(\d+(?:\.\d+)?)\s*(l|liters?|litres?)\b", cleaned)
    if match:
        return float(match.group(1)) * _ML_PER_NORMALIZED_UNIT["l"]

    return None


# ---------------------------------------------------------------------------
# Field comparison building blocks
# ---------------------------------------------------------------------------


def _missing_result(field: str, expected: str) -> FieldResult:
    return FieldResult(
        field=field,
        expected=expected,
        found=None,
        verdict=Verdict.REVIEW,
        reason="Couldn't read this on the label.",
    )


def _compare_text_field(field: str, expected: str, found: str) -> FieldResult:
    norm_expected = normalize_text(expected)
    norm_found = normalize_text(found)

    if norm_expected == norm_found:
        if expected.strip() == found.strip():
            reason = "Matches the application exactly."
        else:
            reason = "Same text; only capitalization or punctuation differs."
        return FieldResult(field=field, expected=expected, found=found, verdict=Verdict.MATCH, reason=reason)

    score = max(
        fuzz.ratio(norm_expected, norm_found),
        fuzz.token_sort_ratio(norm_expected, norm_found),
    )
    if score >= _FUZZY_REVIEW_THRESHOLD:
        return FieldResult(
            field=field,
            expected=expected,
            found=found,
            verdict=Verdict.REVIEW,
            reason=f"Very close but not identical: '{found}' vs '{expected}'.",
        )
    return FieldResult(
        field=field,
        expected=expected,
        found=found,
        verdict=Verdict.MISMATCH,
        reason=f"Label says '{found}' but the application says '{expected}'.",
    )


def _compare_alcohol_content(expected: str, found: str) -> FieldResult:
    field = "Alcohol content"
    expected_abv = parse_abv(expected)
    found_abv = parse_abv(found)

    if expected_abv is None or found_abv is None:
        return FieldResult(
            field=field,
            expected=expected,
            found=found,
            verdict=Verdict.REVIEW,
            reason="Couldn't parse an alcohol percentage from one of these values.",
        )

    if abs(expected_abv - found_abv) > 0.01:
        return FieldResult(
            field=field,
            expected=expected,
            found=found,
            verdict=Verdict.MISMATCH,
            reason=f"Label says {found_abv}% but the application says {expected_abv}%.",
        )

    found_proof = parse_proof(found)
    if found_proof is not None and abs(found_proof - 2 * found_abv) > 0.01:
        return FieldResult(
            field=field,
            expected=expected,
            found=found,
            verdict=Verdict.REVIEW,
            reason=f"The stated proof ({found_proof}) doesn't match twice the ABV ({found_abv}%).",
        )

    return FieldResult(field=field, expected=expected, found=found, verdict=Verdict.MATCH, reason="ABV matches the application.")


def _compare_net_contents(expected: str, found: str) -> FieldResult:
    field = "Net contents"
    expected_ml = parse_net_contents_ml(expected)
    found_ml = parse_net_contents_ml(found)

    if expected_ml is None or found_ml is None:
        return FieldResult(
            field=field,
            expected=expected,
            found=found,
            verdict=Verdict.REVIEW,
            reason="Couldn't parse a volume from one of these values.",
        )

    tolerance = 0.005 * expected_ml
    if abs(expected_ml - found_ml) > tolerance:
        return FieldResult(
            field=field,
            expected=expected,
            found=found,
            verdict=Verdict.MISMATCH,
            reason=f"Label says '{found}' but the application says '{expected}'.",
        )

    return FieldResult(field=field, expected=expected, found=found, verdict=Verdict.MATCH, reason="Net contents match the application.")


def _first_difference_hint(expected: str, found: str, max_words: int = 20) -> str:
    """Return a short hint pointing to where two strings start to differ."""
    expected_words = collapse_whitespace(expected).split(" ")
    found_words = collapse_whitespace(found).split(" ")
    i = 0
    while i < len(expected_words) and i < len(found_words) and expected_words[i] == found_words[i]:
        i += 1
    expected_snip = " ".join(expected_words[i : i + 6]) or "(nothing)"
    found_snip = " ".join(found_words[i : i + 6]) or "(nothing)"
    return f"text differs starting near '{found_snip}' vs required '{expected_snip}'"


def _compare_government_warning(reading: LabelReading) -> FieldResult:
    field = "Government warning"
    expected = GOVERNMENT_WARNING_TEXT
    found = reading.warning_text

    if not found or not found.strip():
        return FieldResult(
            field=field,
            expected=expected,
            found=found,
            verdict=Verdict.MISMATCH,
            reason="No government warning found on the label.",
        )

    found_collapsed = collapse_whitespace(found)

    if not found_collapsed.upper().startswith(GOVERNMENT_WARNING_HEADING.upper()):
        # Doesn't even start with the right heading text.
        return FieldResult(
            field=field,
            expected=expected,
            found=found,
            verdict=Verdict.MISMATCH,
            reason="The warning text doesn't start with the required heading.",
        )

    heading_in_text = found_collapsed[: len(GOVERNMENT_WARNING_HEADING)]
    all_caps = reading.warning_heading_all_caps
    if all_caps is False or heading_in_text != GOVERNMENT_WARNING_HEADING:
        return FieldResult(
            field=field,
            expected=expected,
            found=found,
            verdict=Verdict.MISMATCH,
            reason="The heading must be in all capital letters: 'GOVERNMENT WARNING:'.",
        )

    bold = reading.warning_heading_bold
    if bold is False:
        return FieldResult(
            field=field,
            expected=expected,
            found=found,
            verdict=Verdict.MISMATCH,
            reason="The 'GOVERNMENT WARNING:' heading must be bold.",
        )
    if bold is None:
        return FieldResult(
            field=field,
            expected=expected,
            found=found,
            verdict=Verdict.REVIEW,
            reason="Couldn't confirm the heading is bold.",
        )

    if found_collapsed == expected:
        return FieldResult(field=field, expected=expected, found=found, verdict=Verdict.MATCH, reason="Matches the required warning text exactly.")

    hint = _first_difference_hint(expected, found_collapsed)
    return FieldResult(
        field=field,
        expected=expected,
        found=found,
        verdict=Verdict.MISMATCH,
        reason=f"Wording doesn't match the required text; {hint}.",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compare(application: ApplicationFields, reading: LabelReading) -> list[FieldResult]:
    """Compare every TTB label field against the application, field by field."""
    results: list[FieldResult] = []

    text_fields = [
        ("Brand name", application.brand_name, reading.brand_name),
        ("Class/type", application.class_type, reading.class_type),
        ("Bottler name and address", application.bottler_name_address, reading.bottler_name_address),
        ("Country of origin", application.country_of_origin, reading.country_of_origin),
    ]
    field_order = {
        "Brand name": 0,
        "Class/type": 1,
        "Alcohol content": 2,
        "Net contents": 3,
        "Bottler name and address": 4,
        "Country of origin": 5,
        "Government warning": 6,
    }
    by_field: dict[str, FieldResult] = {}

    for field, expected, found in text_fields:
        if not expected or not expected.strip():
            continue  # optional field left blank on the application: skip entirely
        if not found or not found.strip():
            by_field[field] = _missing_result(field, expected)
            continue
        by_field[field] = _compare_text_field(field, expected, found)

    if application.alcohol_content and application.alcohol_content.strip():
        if not reading.alcohol_content or not reading.alcohol_content.strip():
            by_field["Alcohol content"] = _missing_result("Alcohol content", application.alcohol_content)
        else:
            by_field["Alcohol content"] = _compare_alcohol_content(application.alcohol_content, reading.alcohol_content)

    if application.net_contents and application.net_contents.strip():
        if not reading.net_contents or not reading.net_contents.strip():
            by_field["Net contents"] = _missing_result("Net contents", application.net_contents)
        else:
            by_field["Net contents"] = _compare_net_contents(application.net_contents, reading.net_contents)

    # The government warning is always required, regardless of what's in the
    # application (there's no "expected" field for it on ApplicationFields).
    by_field["Government warning"] = _compare_government_warning(reading)

    results = [by_field[name] for name in sorted(by_field, key=lambda name: field_order[name])]
    return results


def overall(results: list[FieldResult]) -> Verdict:
    """Roll a list of field results up into one overall verdict."""
    if any(r.verdict == Verdict.MISMATCH for r in results):
        return Verdict.MISMATCH
    if any(r.verdict == Verdict.REVIEW for r in results):
        return Verdict.REVIEW
    return Verdict.MATCH
