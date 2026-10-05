"""Generates synthetic alcohol label images and a matching CSV of
application-form values, for exercising the label checker end to end.

Run with: uv run python samples/make_samples.py

All text and layout here is invented for testing; none of it depicts a
real brand, bottler, or product.
"""

from __future__ import annotations

import csv
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).parent
WIDTH, HEIGHT = 900, 1200

CORRECT_WARNING_HEADING = "GOVERNMENT WARNING:"
CORRECT_WARNING_BODY = (
    "(1) According to the Surgeon General, women should not drink alcoholic "
    "beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a "
    "car or operate machinery, and may cause health problems."
)

REWORDED_WARNING_BODY = (
    "(1) According to the Surgeon General, women should not drink alcoholic "
    "beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a "
    "car or operate machinery, and may cause serious health problems."
)

# Fonts available on this box (checked with `fc-list`). Fall back to
# Pillow's built-in bitmap font if neither family is present, so the
# script never hard-fails on a different machine.
FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype/liberation",
]
REGULAR_CANDIDATES = ["DejaVuSans.ttf", "LiberationSans-Regular.ttf"]
BOLD_CANDIDATES = ["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"]


def _find_font(candidates: list[str]) -> str | None:
    for directory in FONT_DIRS:
        for name in candidates:
            path = Path(directory) / name
            if path.exists():
                return str(path)
    return None


REGULAR_PATH = _find_font(REGULAR_CANDIDATES)
BOLD_PATH = _find_font(BOLD_CANDIDATES)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = BOLD_PATH if bold else REGULAR_PATH
    if path is None:
        return ImageFont.load_default()
    return ImageFont.truetype(path, size)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Greedy word wrap using actual glyph widths."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        width = draw.textlength(trial, font=fnt)
        if width <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def new_label(background: tuple[int, int, int] = (250, 247, 238)) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (WIDTH, HEIGHT), background)
    draw = ImageDraw.Draw(img)
    # Simple border to read as a label, not a blank page.
    draw.rectangle([20, 20, WIDTH - 20, HEIGHT - 20], outline=(120, 110, 90), width=3)
    return img, draw


def draw_centered(draw: ImageDraw.ImageDraw, y: int, text: str, fnt: ImageFont.FreeTypeFont, fill=(20, 20, 20)) -> int:
    width = draw.textlength(text, font=fnt)
    x = (WIDTH - width) / 2
    draw.text((x, y), text, font=fnt, fill=fill)
    bbox = fnt.getbbox(text)
    return y + (bbox[3] - bbox[1]) + 18


def draw_warning(
    draw: ImageDraw.ImageDraw,
    top: int,
    heading_text: str,
    heading_bold: bool,
    body_text: str,
) -> None:
    """Draws the Government Warning paragraph, heading then body, inside
    a ruled box near the bottom of the label."""
    margin = 70
    max_width = WIDTH - 2 * margin
    heading_font = font(22, bold=heading_bold)
    body_font = font(19, bold=False)

    draw.rectangle([margin - 15, top - 15, WIDTH - margin + 15, HEIGHT - 60], outline=(90, 90, 90), width=1)

    y = top
    draw.text((margin, y), heading_text, font=heading_font, fill=(10, 10, 10))
    bbox = heading_font.getbbox(heading_text)
    y += (bbox[3] - bbox[1]) + 10

    for line in wrap_text(draw, body_text, body_font, max_width):
        draw.text((margin, y), line, font=body_font, fill=(10, 10, 10))
        bbox = body_font.getbbox(line)
        y += (bbox[3] - bbox[1]) + 8


def base_whiskey_label(
    brand: str = "OLD TOM DISTILLERY",
    class_type: str = "Kentucky Straight Bourbon Whiskey",
    abv_line: str = "45% Alc./Vol. (90 Proof)",
    net_contents: str = "750 mL",
    bottler_line: str = "Bottled by Old Tom Distillery, Bardstown, KY",
    country_line: str | None = None,
    heading_text: str = CORRECT_WARNING_HEADING,
    heading_bold: bool = True,
    warning_body: str = CORRECT_WARNING_BODY,
) -> Image.Image:
    img, draw = new_label()

    y = 90
    y = draw_centered(draw, y, brand, font(46, bold=True))
    y += 10
    y = draw_centered(draw, y, class_type, font(28))
    y += 30
    y = draw_centered(draw, y, abv_line, font(24))
    y = draw_centered(draw, y, net_contents, font(24))
    y += 30
    y = draw_centered(draw, y, bottler_line, font(20))
    if country_line:
        y = draw_centered(draw, y, country_line, font(20))

    draw_warning(draw, HEIGHT - 320, heading_text, heading_bold, warning_body)
    return img


def make_01_old_tom_match() -> Image.Image:
    return base_whiskey_label()


def make_02_stones_throw_case() -> Image.Image:
    # Label prints the brand in all caps; the application has it in
    # normal title case. Same brand, should still match.
    return base_whiskey_label(
        brand="STONE'S THROW",
        class_type="Small Batch Bourbon Whiskey",
        bottler_line="Bottled by Stone's Throw Distilling Co., Louisville, KY",
    )


def make_03_wrong_abv() -> Image.Image:
    # Printed at 40%; the application will say 45% -> mismatch.
    return base_whiskey_label(abv_line="40% Alc./Vol. (80 Proof)")


def make_04_titlecase_warning() -> Image.Image:
    return base_whiskey_label(heading_text="Government Warning:", heading_bold=True)


def make_05_not_bold_warning() -> Image.Image:
    return base_whiskey_label(heading_bold=False)


def make_06_reworded_warning() -> Image.Image:
    return base_whiskey_label(warning_body=REWORDED_WARNING_BODY)


def make_07_import_wine() -> Image.Image:
    img, draw = new_label(background=(248, 245, 240))
    y = 110
    y = draw_centered(draw, y, "CHATEAU MARELLE", font(44, bold=True))
    y += 10
    y = draw_centered(draw, y, "Red Wine", font(28))
    y += 30
    y = draw_centered(draw, y, "13.5% Alc./Vol.", font(24))
    y = draw_centered(draw, y, "750 mL", font(24))
    y += 30
    y = draw_centered(draw, y, "Imported by Marelle Imports, New York, NY", font(20))
    y = draw_centered(draw, y, "Product of France", font(20))
    draw_warning(draw, HEIGHT - 320, CORRECT_WARNING_HEADING, True, CORRECT_WARNING_BODY)
    return img


def make_08_angled_glare() -> Image.Image:
    base = make_01_old_tom_match().convert("RGBA")

    # Rotate a few degrees to simulate a photo taken at an angle. expand=True
    # keeps the whole label in frame; we then composite onto a white canvas.
    rotated = base.rotate(6, expand=True, fillcolor=(255, 255, 255, 255))
    canvas = Image.new("RGBA", rotated.size, (255, 255, 255, 255))
    canvas.alpha_composite(rotated)

    # Soft white glare blob: a blurred radial gradient pasted over one corner.
    glare = Image.new("L", canvas.size, 0)
    glare_draw = ImageDraw.Draw(glare)
    gx, gy, gr = int(canvas.width * 0.65), int(canvas.height * 0.3), 220
    glare_draw.ellipse([gx - gr, gy - gr, gx + gr, gy + gr], fill=160)
    glare = glare.filter(__import__("PIL.ImageFilter", fromlist=["ImageFilter"]).GaussianBlur(60))

    white_layer = Image.new("RGBA", canvas.size, (255, 255, 255, 255))
    canvas = Image.composite(white_layer, canvas, glare)

    return canvas.convert("RGB")


def main() -> None:
    cases = [
        (
            "01_old_tom_match.png",
            make_01_old_tom_match,
            {
                "brand_name": "Old Tom Distillery",
                "class_type": "Kentucky Straight Bourbon Whiskey",
                "alcohol_content": "45% Alc./Vol. (90 Proof)",
                "net_contents": "750 mL",
                "bottler_name_address": "Bottled by Old Tom Distillery, Bardstown, KY",
                "country_of_origin": "",
                "expected_overall": "Match",
            },
        ),
        (
            "02_stones_throw_case.png",
            make_02_stones_throw_case,
            {
                "brand_name": "Stone's Throw",
                "class_type": "Small Batch Bourbon Whiskey",
                "alcohol_content": "45% Alc./Vol. (90 Proof)",
                "net_contents": "750 mL",
                "bottler_name_address": "Bottled by Stone's Throw Distilling Co., Louisville, KY",
                "country_of_origin": "",
                "expected_overall": "Match",
            },
        ),
        (
            "03_wrong_abv.png",
            make_03_wrong_abv,
            {
                "brand_name": "Old Tom Distillery",
                "class_type": "Kentucky Straight Bourbon Whiskey",
                "alcohol_content": "45% Alc./Vol. (90 Proof)",
                "net_contents": "750 mL",
                "bottler_name_address": "Bottled by Old Tom Distillery, Bardstown, KY",
                "country_of_origin": "",
                "expected_overall": "Mismatch",
            },
        ),
        (
            "04_titlecase_warning.png",
            make_04_titlecase_warning,
            {
                "brand_name": "Old Tom Distillery",
                "class_type": "Kentucky Straight Bourbon Whiskey",
                "alcohol_content": "45% Alc./Vol. (90 Proof)",
                "net_contents": "750 mL",
                "bottler_name_address": "Bottled by Old Tom Distillery, Bardstown, KY",
                "country_of_origin": "",
                "expected_overall": "Mismatch",
            },
        ),
        (
            "05_not_bold_warning.png",
            make_05_not_bold_warning,
            {
                "brand_name": "Old Tom Distillery",
                "class_type": "Kentucky Straight Bourbon Whiskey",
                "alcohol_content": "45% Alc./Vol. (90 Proof)",
                "net_contents": "750 mL",
                "bottler_name_address": "Bottled by Old Tom Distillery, Bardstown, KY",
                "country_of_origin": "",
                "expected_overall": "Mismatch",
            },
        ),
        (
            "06_reworded_warning.png",
            make_06_reworded_warning,
            {
                "brand_name": "Old Tom Distillery",
                "class_type": "Kentucky Straight Bourbon Whiskey",
                "alcohol_content": "45% Alc./Vol. (90 Proof)",
                "net_contents": "750 mL",
                "bottler_name_address": "Bottled by Old Tom Distillery, Bardstown, KY",
                "country_of_origin": "",
                "expected_overall": "Mismatch",
            },
        ),
        (
            "07_import_wine.png",
            make_07_import_wine,
            {
                "brand_name": "Chateau Marelle",
                "class_type": "Red Wine",
                "alcohol_content": "13.5% Alc./Vol.",
                "net_contents": "750 mL",
                "bottler_name_address": "Imported by Marelle Imports, New York, NY",
                "country_of_origin": "Product of France",
                "expected_overall": "Match",
            },
        ),
        (
            "08_angled_glare.png",
            make_08_angled_glare,
            {
                "brand_name": "Old Tom Distillery",
                "class_type": "Kentucky Straight Bourbon Whiskey",
                "alcohol_content": "45% Alc./Vol. (90 Proof)",
                "net_contents": "750 mL",
                "bottler_name_address": "Bottled by Old Tom Distillery, Bardstown, KY",
                "country_of_origin": "",
                "expected_overall": "Match",
            },
        ),
    ]

    rows = []
    for filename, make_fn, fields in cases:
        image = make_fn()
        image.save(OUT_DIR / filename)
        rows.append({"image": filename, **fields})
        print(f"wrote {filename}")

    csv_path = OUT_DIR / "labels.csv"
    fieldnames = [
        "image",
        "brand_name",
        "class_type",
        "alcohol_content",
        "net_contents",
        "bottler_name_address",
        "country_of_origin",
        "expected_overall",
    ]
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {csv_path.name}")


if __name__ == "__main__":
    main()
