"""Streamlit screens for TTB Label Check.

Two tabs:
- Check one label: upload one label photo, type in the application details,
  and see a field-by-field comparison.
- Check a batch: upload a spreadsheet of application details plus many label
  images, and check them all at once.

The screens here only collect input and display results. All the actual
decisions (reading the label, comparing fields) live in labelcheck/.
"""

from __future__ import annotations

import hashlib
import io
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import streamlit as st

from labelcheck.models import ApplicationFields, LabelReading, Verdict
from labelcheck.reader import ReadError, read_label, warm_up
from labelcheck.rules import compare, overall

# How many labels to read at once during a batch run.
MAX_WORKERS = 8

_EXT_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

REQUIRED_FIELD_LABELS = {
    "brand_name": "Brand name",
    "class_type": "Class/type",
    "alcohol_content": "Alcohol content",
    "net_contents": "Net contents",
}

REQUIRED_BATCH_COLUMNS = ["image", "brand_name", "class_type", "alcohol_content", "net_contents"]
OPTIONAL_BATCH_COLUMNS = ["bottler_name_address", "country_of_origin"]


# ---------------------------------------------------------------------------
# Plain functions (no Streamlit calls) - these are what the tests exercise.
# ---------------------------------------------------------------------------


def guess_media_type(filename: str) -> str:
    """Guess an image's media type from its file extension."""
    ext = os.path.splitext(filename)[1].lower()
    return _EXT_MEDIA_TYPES.get(ext, "image/png")


def validate_single_inputs(form_values: dict, image_bytes: bytes | None) -> list[str]:
    """Return plain-language problems with the single-label form, if any."""
    errors = []
    for key, label in REQUIRED_FIELD_LABELS.items():
        if not (form_values.get(key) or "").strip():
            errors.append(f"{label} is required.")
    if not image_bytes:
        errors.append("Please upload a label image.")
    return errors


def check_one(application: ApplicationFields, image_bytes: bytes, media_type: str, reader=None) -> dict:
    """Read one label and compare it to the application. Raises ReadError."""
    reader = reader or read_label
    start = time.monotonic()
    reading = reader(image_bytes, media_type)
    elapsed = time.monotonic() - start
    results = compare(application, reading)
    verdict = overall(results)
    return {"reading": reading, "results": results, "verdict": verdict, "elapsed": elapsed}


def parse_batch_csv(csv_bytes: bytes) -> list[dict]:
    """Parse the application spreadsheet into a list of plain dicts.

    Extra columns (e.g. a sample's `expected_overall`) are ignored.
    Raises ValueError if a required column is missing.
    """
    df = pd.read_csv(io.BytesIO(csv_bytes), dtype=str).fillna("")
    missing_cols = [c for c in REQUIRED_BATCH_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ValueError(f"Spreadsheet is missing column(s): {', '.join(missing_cols)}")
    for col in OPTIONAL_BATCH_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[REQUIRED_BATCH_COLUMNS + OPTIONAL_BATCH_COLUMNS].to_dict(orient="records")


def match_rows_to_images(rows: list[dict], image_names: set[str]) -> tuple[list[dict], list[dict]]:
    """Split spreadsheet rows into those with a matching uploaded image and those without."""
    matched = [row for row in rows if row.get("image") in image_names]
    missing = [row for row in rows if row.get("image") not in image_names]
    return matched, missing


def _row_to_application(row: dict) -> ApplicationFields:
    return ApplicationFields(
        brand_name=row.get("brand_name", ""),
        class_type=row.get("class_type", ""),
        alcohol_content=row.get("alcohol_content", ""),
        net_contents=row.get("net_contents", ""),
        bottler_name_address=row.get("bottler_name_address", "") or "",
        country_of_origin=row.get("country_of_origin", "") or "",
    )


def _problem_summary(results) -> str:
    return "; ".join(f"{r.field}: {r.reason}" for r in results if r.verdict != Verdict.MATCH)


def _process_row(row: dict, images: dict[str, bytes], reader=None) -> dict:
    """Read and compare one batch row. Never raises; a read failure is 'Needs review'."""
    reader = reader or read_label
    image_name = row.get("image", "")
    image_bytes = images.get(image_name)
    if image_bytes is None:
        message = "Image file not found in upload"
        return {"image": image_name, "overall": Verdict.REVIEW.value, "problem_fields": message, "error": message}

    try:
        reading = reader(image_bytes, guess_media_type(image_name))
    except ReadError as exc:
        return {"image": image_name, "overall": Verdict.REVIEW.value, "problem_fields": str(exc), "error": str(exc)}

    results = compare(_row_to_application(row), reading)
    verdict = overall(results)
    return {"image": image_name, "overall": verdict.value, "problem_fields": _problem_summary(results), "error": None}


def run_batch(rows: list[dict], images: dict[str, bytes], reader=None, max_workers=MAX_WORKERS, on_progress=None) -> list[dict]:
    """Check many labels concurrently. Returns one result dict per row, in row order."""
    results: list[dict | None] = [None] * len(rows)
    total = len(rows)
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_index = {pool.submit(_process_row, row, images, reader): i for i, row in enumerate(rows)}
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            results[index] = future.result()
            done += 1
            if on_progress:
                on_progress(done, total)
    return results  # type: ignore[return-value]


def build_results_csv(batch_results: list[dict]) -> bytes:
    """Build a downloadable CSV, one row per label."""
    df = pd.DataFrame(
        [
            {"image": r["image"], "overall": r["overall"], "problem_fields": r["problem_fields"]}
            for r in batch_results
        ]
    )
    return df.to_csv(index=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Streamlit screens
# ---------------------------------------------------------------------------


LABEL_PANEL_FIELDS = {
    "brand_name": "Brand name",
    "class_type": "Class/type",
    "alcohol_content": "Alcohol content",
    "net_contents": "Net contents",
    "bottler_name_address": "Bottler name and address",
    "country_of_origin": "Country of origin",
    "warning_text": "Government warning",
}


def render_label_panel(reading: LabelReading) -> None:
    """Show what the AI read, so the agent can spot a misread before trusting the result."""
    st.subheader("What the label says")
    rows = [
        {"Field": label, "Read from label": getattr(reading, key) or "(not found)"}
        for key, label in LABEL_PANEL_FIELDS.items()
    ]
    rows.append({"Field": "Warning heading in capitals", "Read from label": _yes_no(reading.warning_heading_all_caps)})
    rows.append({"Field": "Warning heading in bold", "Read from label": _yes_no(reading.warning_heading_bold)})
    st.dataframe(rows, use_container_width=True, hide_index=True)
    if reading.image_quality_note:
        st.info(f"Image quality note: {reading.image_quality_note}")


def _yes_no(value: bool | None) -> str:
    return "Not sure" if value is None else ("Yes" if value else "No")


def render_single_result(result: dict) -> None:
    st.subheader("Result")
    verdict = result["verdict"]
    if verdict == Verdict.MATCH:
        st.success("Match: the label matches the application.")
    elif verdict == Verdict.MISMATCH:
        st.error("Mismatch: the label does not match the application.")
    else:
        st.warning("Needs review: some fields could not be confirmed.")

    # The label values are already in the panel above and the application values
    # in the form, so this table sticks to the verdict and the reason.
    table_rows = [{"Field": r.field, "Result": r.verdict.value, "Why": r.reason} for r in result["results"]]
    st.dataframe(table_rows, use_container_width=True, hide_index=True)


def _read_uploaded_label(uploaded_image) -> LabelReading | None:
    """Read the label once per uploaded file and keep the result for this session.

    Reading starts as soon as the image is uploaded, so it usually finishes while
    the agent is still typing the application details.
    """
    image_bytes = uploaded_image.getvalue()
    key = hashlib.sha256(image_bytes).hexdigest()
    cache = st.session_state.setdefault("label_readings", {})
    if key not in cache:
        media_type = getattr(uploaded_image, "type", None) or guess_media_type(uploaded_image.name)
        start = time.monotonic()
        with st.spinner("Reading the label..."):
            try:
                cache[key] = (read_label(image_bytes, media_type), time.monotonic() - start)
            except ReadError as exc:
                st.error(str(exc))
                return None
    reading, elapsed = cache[key]
    st.caption(f"Label read in {elapsed:.1f} seconds.")
    return reading


def render_single_tab() -> None:
    with st.expander("How to use"):
        st.write(
            "Upload a photo of the label. The app reads it right away and shows what it found. "
            "Type in what the application says for each field, then click **Check label** to "
            "see whether each field matches, number for number and word for word."
        )

    left, right = st.columns(2, gap="large")

    with left:
        uploaded_image = st.file_uploader("Label image", type=["png", "jpg", "jpeg", "webp"], key="single_image")
        if uploaded_image is not None:
            st.image(uploaded_image, caption="Uploaded label", width=260)

        with st.form("single_label_form"):
            brand_name = st.text_input("Brand name", key="single_brand_name")
            class_type = st.text_input("Class/type", key="single_class_type")
            alcohol_content = st.text_input("Alcohol content", key="single_alcohol_content")
            net_contents = st.text_input("Net contents", key="single_net_contents")
            bottler_name_address = st.text_input("Bottler name and address (optional)", key="single_bottler")
            country_of_origin = st.text_input("Country of origin (optional, imports only)", key="single_country")
            submitted = st.form_submit_button("Check label", type="primary", use_container_width=True)

    with right:
        reading = _read_uploaded_label(uploaded_image) if uploaded_image is not None else None
        if reading is not None:
            render_label_panel(reading)
        elif uploaded_image is None:
            st.info("Upload a label image to see what the label says.")

        if submitted:
            form_values = {
                "brand_name": brand_name,
                "class_type": class_type,
                "alcohol_content": alcohol_content,
                "net_contents": net_contents,
            }
            image_bytes = uploaded_image.getvalue() if uploaded_image is not None else None
            errors = validate_single_inputs(form_values, image_bytes)
            st.session_state["single_result"] = None
            if errors:
                for message in errors:
                    st.error(message)
            elif reading is not None:
                application = ApplicationFields(
                    brand_name=brand_name.strip(),
                    class_type=class_type.strip(),
                    alcohol_content=alcohol_content.strip(),
                    net_contents=net_contents.strip(),
                    bottler_name_address=bottler_name_address.strip(),
                    country_of_origin=country_of_origin.strip(),
                )
                result = check_one(application, image_bytes, "", reader=lambda *_: reading)
                result["image_key"] = hashlib.sha256(image_bytes).hexdigest()
                st.session_state["single_result"] = result

        # Only show a result that belongs to the image currently uploaded.
        result = st.session_state.get("single_result")
        current_key = hashlib.sha256(uploaded_image.getvalue()).hexdigest() if uploaded_image else None
        if result and result.get("image_key") == current_key:
            render_single_result(result)


def render_batch_results(batch_results: list[dict]) -> None:
    counts = {"Match": 0, "Mismatch": 0, "Needs review": 0}
    for row in batch_results:
        counts[row["overall"]] = counts.get(row["overall"], 0) + 1

    col1, col2, col3 = st.columns(3)
    col1.metric("Match", counts["Match"])
    col2.metric("Mismatch", counts["Mismatch"])
    col3.metric("Needs review", counts["Needs review"])

    table_rows = [
        {"Image": row["image"], "Overall result": row["overall"], "Problem fields": row["problem_fields"] or "-"}
        for row in batch_results
    ]
    st.dataframe(table_rows, use_container_width=True, hide_index=True)

    st.download_button(
        "Download results as CSV",
        data=build_results_csv(batch_results),
        file_name="label_check_results.csv",
        mime="text/csv",
    )


def render_batch_tab() -> None:
    with st.expander("How to use"):
        st.write(
            "Upload the application spreadsheet (CSV) and all the label photos it refers to. "
            "The app matches each row to its image by file name. Click **Check all labels** "
            "to read and compare every label at once, then download the results."
        )

    csv_file = st.file_uploader("Application spreadsheet (CSV)", type=["csv"], key="batch_csv")
    image_files = st.file_uploader(
        "Label images", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True, key="batch_images"
    )

    if csv_file is None:
        return

    try:
        rows = parse_batch_csv(csv_file.getvalue())
    except ValueError as exc:
        st.error(str(exc))
        return

    images = {f.name: f.getvalue() for f in (image_files or [])}
    matched, missing = match_rows_to_images(rows, set(images.keys()))

    st.write(f"{len(matched)} of {len(rows)} rows have a matching image.")
    if missing:
        st.warning("These rows have no matching image file, and will be skipped:")
        st.dataframe([{"Image file expected": row["image"]} for row in missing], use_container_width=True, hide_index=True)

    run_clicked = st.button("Check all labels", type="primary", use_container_width=True, disabled=not matched)
    if run_clicked:
        progress = st.progress(0.0, text="Checking labels...")

        def on_progress(done: int, total: int) -> None:
            progress.progress(done / total, text=f"Checked {done} of {total} labels")

        st.session_state["batch_results"] = run_batch(matched, images, on_progress=on_progress)
        progress.empty()

    batch_results = st.session_state.get("batch_results")
    if batch_results:
        render_batch_results(batch_results)


@st.cache_resource
def _warm_up_once() -> bool:
    warm_up()
    return True


def _load_api_key() -> None:
    """Use the key from Streamlit's secrets settings when it isn't already in the environment."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY")
    except FileNotFoundError:  # no secrets file when running locally
        key = None
    if key:
        os.environ["ANTHROPIC_API_KEY"] = key


def main() -> None:
    st.set_page_config(page_title="TTB Label Check", page_icon=":label:", layout="wide")
    _load_api_key()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        st.error("The app isn't set up yet: no API key is configured. Add ANTHROPIC_API_KEY in the app's secrets settings.")
        st.stop()
    _warm_up_once()
    st.title("TTB Label Check")
    st.write("This app checks whether an alcohol label matches its application, field by field.")

    tab_single, tab_batch = st.tabs(["Check one label", "Check a batch"])
    with tab_single:
        render_single_tab()
    with tab_batch:
        render_batch_tab()


if __name__ == "__main__":
    main()
