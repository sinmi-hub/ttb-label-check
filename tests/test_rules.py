"""Tests for labelcheck.rules: deterministic comparison of application vs. label."""

from labelcheck.models import ApplicationFields, LabelReading, Verdict
from labelcheck.rules import GOVERNMENT_WARNING_TEXT, compare, overall


def _application(**overrides) -> ApplicationFields:
    base = dict(
        brand_name="Old Tom Distillery",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45%",
        net_contents="750 mL",
        bottler_name_address="",
        country_of_origin="",
    )
    base.update(overrides)
    return ApplicationFields(**base)


def _reading(**overrides) -> LabelReading:
    base = dict(
        brand_name="Old Tom Distillery",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
        bottler_name_address=None,
        country_of_origin=None,
        warning_text=GOVERNMENT_WARNING_TEXT,
        warning_heading_all_caps=True,
        warning_heading_bold=True,
        image_quality_note=None,
    )
    base.update(overrides)
    return LabelReading(**base)


def _result(results, field_name):
    for r in results:
        if r.field == field_name:
            return r
    raise AssertionError(f"no result for field {field_name}")


# ---------------------------------------------------------------------------
# Field order / overall structure
# ---------------------------------------------------------------------------


def test_field_order_and_full_application():
    app = _application(bottler_name_address="123 Main St", country_of_origin="USA")
    reading = _reading(bottler_name_address="123 Main St", country_of_origin="USA")
    results = compare(app, reading)
    assert [r.field for r in results] == [
        "Brand name",
        "Class/type",
        "Alcohol content",
        "Net contents",
        "Bottler name and address",
        "Country of origin",
        "Government warning",
    ]
    assert overall(results) == Verdict.MATCH


def test_optional_fields_skipped_when_blank_on_application():
    app = _application()  # bottler and country blank
    reading = _reading()
    results = compare(app, reading)
    fields = [r.field for r in results]
    assert "Bottler name and address" not in fields
    assert "Country of origin" not in fields


# ---------------------------------------------------------------------------
# Text field normalization / fuzzy matching
# ---------------------------------------------------------------------------


def test_stones_throw_curly_apostrophe_and_case_match():
    app = _application(brand_name="Stone's Throw")
    reading = _reading(brand_name="STONE’S THROW")
    result = _result(compare(app, reading), "Brand name")
    assert result.verdict == Verdict.MATCH
    assert "capitalization" in result.reason or "punctuation" in result.reason


def test_exact_text_match_gets_simple_reason():
    app = _application(brand_name="Old Tom Distillery")
    reading = _reading(brand_name="Old Tom Distillery")
    result = _result(compare(app, reading), "Brand name")
    assert result.verdict == Verdict.MATCH
    assert "exactly" in result.reason


def test_close_but_not_identical_brand_name_goes_to_review():
    app = _application(brand_name="Old Tom Distillery")
    reading = _reading(brand_name="Old Tom Distillry")  # typo, very close
    result = _result(compare(app, reading), "Brand name")
    assert result.verdict == Verdict.REVIEW
    assert "Very close" in result.reason


def test_clearly_different_brand_name_is_mismatch():
    app = _application(brand_name="Old Tom Distillery")
    reading = _reading(brand_name="New River Spirits")
    result = _result(compare(app, reading), "Brand name")
    assert result.verdict == Verdict.MISMATCH


def test_class_type_mismatch():
    app = _application(class_type="Kentucky Straight Bourbon Whiskey")
    reading = _reading(class_type="Blended Scotch Whisky")
    result = _result(compare(app, reading), "Class/type")
    assert result.verdict == Verdict.MISMATCH


def test_bottler_address_minor_punctuation_difference_matches():
    app = _application(bottler_name_address="123 Main St., Louisville, KY")
    reading = _reading(bottler_name_address="123 Main St Louisville KY")
    result = _result(compare(app, reading), "Bottler name and address")
    assert result.verdict == Verdict.MATCH


def test_country_of_origin_mismatch():
    app = _application(country_of_origin="France")
    reading = _reading(country_of_origin="Scotland")
    result = _result(compare(app, reading), "Country of origin")
    assert result.verdict == Verdict.MISMATCH


# ---------------------------------------------------------------------------
# Missing / unreadable fields
# ---------------------------------------------------------------------------


def test_missing_label_reading_for_declared_field_is_review():
    app = _application(bottler_name_address="123 Main St")
    reading = _reading(bottler_name_address=None)
    result = _result(compare(app, reading), "Bottler name and address")
    assert result.verdict == Verdict.REVIEW
    assert "Couldn't read" in result.reason


def test_missing_alcohol_content_reading_is_review():
    app = _application()
    reading = _reading(alcohol_content=None)
    result = _result(compare(app, reading), "Alcohol content")
    assert result.verdict == Verdict.REVIEW


def test_missing_net_contents_reading_is_review():
    app = _application()
    reading = _reading(net_contents=None)
    result = _result(compare(app, reading), "Net contents")
    assert result.verdict == Verdict.REVIEW


# ---------------------------------------------------------------------------
# Alcohol content parsing
# ---------------------------------------------------------------------------


def test_abv_matches_with_proof_statement():
    app = _application(alcohol_content="45% ABV")
    reading = _reading(alcohol_content="45% Alc./Vol. (90 Proof)")
    result = _result(compare(app, reading), "Alcohol content")
    assert result.verdict == Verdict.MATCH


def test_abv_matches_alc_by_vol_phrasing():
    app = _application(alcohol_content="45%")
    reading = _reading(alcohol_content="Alc. 45% by Vol")
    result = _result(compare(app, reading), "Alcohol content")
    assert result.verdict == Verdict.MATCH


def test_abv_proof_mismatch_flagged_for_review():
    app = _application(alcohol_content="45%")
    reading = _reading(alcohol_content="45% Alc./Vol. (86 Proof)")
    result = _result(compare(app, reading), "Alcohol content")
    assert result.verdict == Verdict.REVIEW


def test_abv_numbers_differ_is_mismatch():
    app = _application(alcohol_content="45%")
    reading = _reading(alcohol_content="40% Alc./Vol.")
    result = _result(compare(app, reading), "Alcohol content")
    assert result.verdict == Verdict.MISMATCH


def test_abv_unparseable_is_review():
    app = _application(alcohol_content="45%")
    reading = _reading(alcohol_content="high proof")
    result = _result(compare(app, reading), "Alcohol content")
    assert result.verdict == Verdict.REVIEW


# ---------------------------------------------------------------------------
# Net contents parsing / conversion
# ---------------------------------------------------------------------------


def test_net_contents_ml_variants_match():
    app = _application(net_contents="750 mL")
    for label_value in ["750ml", "0.75 L", "0.75 liter", "75 cl"]:
        reading = _reading(net_contents=label_value)
        result = _result(compare(app, reading), "Net contents")
        assert result.verdict == Verdict.MATCH, f"failed for {label_value}"


def test_net_contents_fl_oz_matches_ml():
    app = _application(net_contents="25.4 fl oz")
    reading = _reading(net_contents="750 mL")
    result = _result(compare(app, reading), "Net contents")
    assert result.verdict == Verdict.MATCH


def test_net_contents_mismatch():
    app = _application(net_contents="750 mL")
    reading = _reading(net_contents="500 mL")
    result = _result(compare(app, reading), "Net contents")
    assert result.verdict == Verdict.MISMATCH


def test_net_contents_unparseable_is_review():
    app = _application(net_contents="750 mL")
    reading = _reading(net_contents="a lot")
    result = _result(compare(app, reading), "Net contents")
    assert result.verdict == Verdict.REVIEW


# ---------------------------------------------------------------------------
# Government warning
# ---------------------------------------------------------------------------


def test_government_warning_exact_match():
    app = _application()
    reading = _reading(warning_text=GOVERNMENT_WARNING_TEXT, warning_heading_all_caps=True, warning_heading_bold=True)
    result = _result(compare(app, reading), "Government warning")
    assert result.verdict == Verdict.MATCH


def test_government_warning_title_case_heading_rejected():
    app = _application()
    bad_text = GOVERNMENT_WARNING_TEXT.replace("GOVERNMENT WARNING:", "Government Warning:")
    reading = _reading(warning_text=bad_text, warning_heading_all_caps=False, warning_heading_bold=True)
    result = _result(compare(app, reading), "Government warning")
    assert result.verdict == Verdict.MISMATCH
    assert "capital" in result.reason


def test_government_warning_non_bold_heading_rejected():
    app = _application()
    reading = _reading(warning_text=GOVERNMENT_WARNING_TEXT, warning_heading_all_caps=True, warning_heading_bold=False)
    result = _result(compare(app, reading), "Government warning")
    assert result.verdict == Verdict.MISMATCH
    assert "bold" in result.reason


def test_government_warning_bold_unknown_is_review():
    app = _application()
    reading = _reading(warning_text=GOVERNMENT_WARNING_TEXT, warning_heading_all_caps=True, warning_heading_bold=None)
    result = _result(compare(app, reading), "Government warning")
    assert result.verdict == Verdict.REVIEW
    assert "bold" in result.reason


def test_government_warning_reworded_text_rejected():
    app = _application()
    bad_text = GOVERNMENT_WARNING_TEXT.replace(
        "women should not drink alcoholic beverages during pregnancy",
        "women should avoid alcoholic beverages while pregnant",
    )
    reading = _reading(warning_text=bad_text, warning_heading_all_caps=True, warning_heading_bold=True)
    result = _result(compare(app, reading), "Government warning")
    assert result.verdict == Verdict.MISMATCH


def test_government_warning_missing_is_mismatch():
    app = _application()
    reading = _reading(warning_text=None)
    result = _result(compare(app, reading), "Government warning")
    assert result.verdict == Verdict.MISMATCH
    assert "No government warning" in result.reason


# ---------------------------------------------------------------------------
# overall()
# ---------------------------------------------------------------------------


def test_overall_mismatch_dominates():
    app = _application(bottler_name_address="123 Main St")
    reading = _reading(bottler_name_address=None, brand_name="Totally Different Brand")
    results = compare(app, reading)
    assert overall(results) == Verdict.MISMATCH


def test_overall_review_when_no_mismatch_but_some_review():
    app = _application(bottler_name_address="123 Main St")
    reading = _reading(bottler_name_address=None)
    results = compare(app, reading)
    assert overall(results) == Verdict.REVIEW


def test_overall_match_when_everything_matches():
    app = _application()
    reading = _reading()
    results = compare(app, reading)
    assert overall(results) == Verdict.MATCH
