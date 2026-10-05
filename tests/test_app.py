"""Tests for the Streamlit screens in app.py.

The non-UI logic (check_one, run_batch, CSV parsing/matching) is factored
into plain functions, so most of this is tested directly against those
functions with labelcheck.reader.read_label monkeypatched - no network calls,
no API key needed.

A couple of tests drive the single-label tab end to end with
streamlit.testing.v1.AppTest, since that flow doesn't need a real file
upload to exercise validation and error handling.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import app
from labelcheck.models import LabelReading, Verdict
from labelcheck.reader import ReadError
from labelcheck.rules import GOVERNMENT_WARNING_TEXT

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def _application(**overrides):
    base = dict(
        brand_name="Old Tom Distillery",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45%",
        net_contents="750 mL",
        bottler_name_address="",
        country_of_origin="",
    )
    base.update(overrides)
    return app.ApplicationFields(**base)


def _matching_reading(**overrides) -> LabelReading:
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


# ---------------------------------------------------------------------------
# check_one
# ---------------------------------------------------------------------------


def test_check_one_returns_match_for_fully_matching_reading():
    reading = _matching_reading()
    result = app.check_one(_application(), b"fake-bytes", "image/png", reader=lambda b, m: reading)

    assert result["verdict"] == Verdict.MATCH
    assert result["reading"] is reading
    assert result["elapsed"] >= 0
    assert [r.field for r in result["results"]][:4] == [
        "Brand name",
        "Class/type",
        "Alcohol content",
        "Net contents",
    ]


def test_check_one_propagates_read_error():
    def failing_reader(image_bytes, media_type):
        raise ReadError("Couldn't reach the AI service")

    with pytest.raises(ReadError, match="Couldn't reach the AI service"):
        app.check_one(_application(), b"fake-bytes", "image/png", reader=failing_reader)


# ---------------------------------------------------------------------------
# validate_single_inputs
# ---------------------------------------------------------------------------


def test_validate_single_inputs_flags_missing_required_fields_and_image():
    errors = app.validate_single_inputs({"brand_name": "", "class_type": "Bourbon"}, image_bytes=None)

    assert "Brand name is required." in errors
    assert "Please upload a label image." in errors
    assert not any("Class/type" in e for e in errors)


def test_validate_single_inputs_passes_when_everything_present():
    form_values = {
        "brand_name": "Old Tom",
        "class_type": "Bourbon",
        "alcohol_content": "45%",
        "net_contents": "750 mL",
    }
    assert app.validate_single_inputs(form_values, image_bytes=b"abc") == []


# ---------------------------------------------------------------------------
# Batch CSV parsing and matching
# ---------------------------------------------------------------------------


def test_parse_batch_csv_ignores_extra_columns_and_fills_optional_ones():
    csv_bytes = (
        b"image,brand_name,class_type,alcohol_content,net_contents,expected_overall\n"
        b"01.png,Old Tom,Bourbon,45%,750 mL,Match\n"
    )
    rows = app.parse_batch_csv(csv_bytes)

    assert len(rows) == 1
    assert rows[0]["image"] == "01.png"
    assert rows[0]["bottler_name_address"] == ""
    assert "expected_overall" not in rows[0]


def test_parse_batch_csv_raises_on_missing_required_column():
    csv_bytes = b"image,brand_name\n01.png,Old Tom\n"
    with pytest.raises(ValueError, match="missing column"):
        app.parse_batch_csv(csv_bytes)


def test_match_rows_to_images_splits_matched_and_missing():
    rows = [{"image": "01.png"}, {"image": "02.png"}]
    matched, missing = app.match_rows_to_images(rows, {"01.png"})

    assert matched == [{"image": "01.png"}]
    assert missing == [{"image": "02.png"}]


# ---------------------------------------------------------------------------
# run_batch
# ---------------------------------------------------------------------------


def _batch_row(image="01.png", **overrides):
    row = {
        "image": image,
        "brand_name": "Old Tom Distillery",
        "class_type": "Kentucky Straight Bourbon Whiskey",
        "alcohol_content": "45%",
        "net_contents": "750 mL",
        "bottler_name_address": "",
        "country_of_origin": "",
    }
    row.update(overrides)
    return row


def test_run_batch_matches_and_reports_read_errors():
    rows = [_batch_row(image="01.png"), _batch_row(image="02.png")]
    images = {"01.png": b"bytes-1", "02.png": b"bytes-2"}

    def reader(image_bytes, media_type):
        if image_bytes == b"bytes-1":
            return _matching_reading()
        raise ReadError("The AI response was cut off before it finished")

    results = app.run_batch(rows, images, reader=reader, max_workers=2)
    by_image = {r["image"]: r for r in results}

    assert by_image["01.png"]["overall"] == Verdict.MATCH.value
    assert by_image["01.png"]["error"] is None
    assert by_image["02.png"]["overall"] == Verdict.REVIEW.value
    assert "cut off" in by_image["02.png"]["error"]


def test_run_batch_flags_rows_with_no_matching_image():
    rows = [_batch_row(image="missing.png")]
    results = app.run_batch(rows, images={}, reader=lambda b, m: _matching_reading())

    assert results[0]["overall"] == Verdict.REVIEW.value
    assert "not found" in results[0]["error"]


def test_build_results_csv_has_expected_header():
    results = [{"image": "01.png", "overall": "Match", "problem_fields": ""}]
    csv_text = app.build_results_csv(results).decode("utf-8")

    assert csv_text.splitlines()[0] == "image,overall,problem_fields"


# ---------------------------------------------------------------------------
# AppTest: drive the single-label tab (no file upload needed for these)
# ---------------------------------------------------------------------------


def test_single_tab_shows_validation_message_when_fields_and_image_missing(monkeypatch):
    at = AppTest.from_file(APP_PATH)
    at.run()

    # Submit the form with everything left blank.
    submit_buttons = [b for b in at.button if "Check label" in (b.label or "")]
    assert submit_buttons, "expected to find the single-label submit button"
    submit_buttons[0].click().run()

    errors = [e.value for e in at.error]
    assert any("Brand name is required." in e for e in errors)
    assert any("upload a label image" in e for e in errors)


def test_single_tab_runs_without_exception():
    at = AppTest.from_file(APP_PATH)
    at.run()

    assert not at.exception
