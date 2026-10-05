"""Reads a label image with Claude and returns a structured LabelReading."""

from __future__ import annotations

import base64
import io
import logging
import threading
import time

import anthropic
from PIL import Image

from labelcheck.models import LabelReading

logger = logging.getLogger(__name__)

MODEL = "claude-opus-5"
MAX_LONG_SIDE = 1568
REQUEST_TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 2

# Betas required for the server-side refusal-fallback "default" form (scalar,
# not the array form, which uses a different header - see model-migration.md).
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

ALLOWED_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

SYSTEM_PROMPT = (
    "You are reading a photograph of a US alcohol beverage label for a TTB "
    "compliance check. Transcribe printed text exactly as it appears, "
    "including capitalization and punctuation. Do not correct spelling, "
    "normalize formatting, or guess at text you cannot read clearly. If a "
    "field is not present on the label or is unreadable, return null for it. "
    "For the health warning, judge whether the heading 'GOVERNMENT WARNING:' "
    "is printed entirely in capital letters and whether it is visually bold "
    "(heavier weight than the surrounding body text). If glare, a steep "
    "angle, blur, or cropping makes any part of the label hard to read, "
    "describe the problem briefly in image_quality_note."
)

USER_PROMPT = (
    "Read this alcohol beverage label and extract the fields in the "
    "requested schema."
)

_client: anthropic.Anthropic | None = None


class ReadError(Exception):
    """Raised when the label image could not be read into a LabelReading."""


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=MAX_RETRIES,
        )
    return _client


def warm_up() -> None:
    """Open the connection to the API ahead of the first label check.

    The first request otherwise pays for the TLS handshake and connection setup
    (seen as roughly 11 s versus 4 s afterwards). A cheap model lookup in a
    background thread does that work while the agent is still filling the form.
    """

    def _ping() -> None:
        try:
            _get_client().models.retrieve(MODEL)
        except Exception:  # best effort only; the first real check will connect instead
            logger.info("Warm-up request failed")

    threading.Thread(target=_ping, daemon=True).start()


def _downscale(image_bytes: bytes, media_type: str) -> tuple[bytes, str]:
    """Shrink the image so its longest side is at most MAX_LONG_SIDE px.

    Returns possibly-re-encoded bytes and the (possibly updated) media type.
    GIFs are left alone since Pillow's resize would drop animation/transparency
    semantics we don't need to touch here, and GIF labels are rare.
    """
    if media_type == "image/gif":
        return image_bytes, media_type

    with Image.open(io.BytesIO(image_bytes)) as img:
        width, height = img.size
        longest = max(width, height)
        if longest <= MAX_LONG_SIDE:
            return image_bytes, media_type

        scale = MAX_LONG_SIDE / longest
        new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        resized = img.convert("RGB") if img.mode in ("P", "RGBA", "LA") else img
        resized = resized.resize(new_size, Image.LANCZOS)

        buffer = io.BytesIO()
        fmt = "JPEG" if media_type in ("image/jpeg", "image/webp") else "PNG"
        if fmt == "JPEG" and resized.mode != "RGB":
            resized = resized.convert("RGB")
        resized.save(buffer, format=fmt)
        new_media_type = "image/jpeg" if fmt == "JPEG" else "image/png"
        return buffer.getvalue(), new_media_type


def read_label(image_bytes: bytes, media_type: str) -> LabelReading:
    """Send a label image to Claude and return a structured LabelReading.

    Raises ReadError on any failure: bad input, network/API errors, a model
    refusal, or a response that was cut off before it could be parsed.
    """
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise ReadError(f"Unsupported image type: {media_type}")

    try:
        resized_bytes, resized_media_type = _downscale(image_bytes, media_type)
    except Exception as exc:
        raise ReadError("Couldn't read that image file") from exc

    encoded = base64.standard_b64encode(resized_bytes).decode("utf-8")

    client = _get_client()
    start = time.monotonic()
    try:
        response = client.beta.messages.parse(
            model=MODEL,
            max_tokens=4096,
            betas=[_FALLBACK_BETA],
            fallbacks="default",
            # Opus 5 thinking defaults to adaptive; we leave `thinking` unset
            # rather than disabling it, as the docs recommend for this model.
            output_config={"effort": "low"},
            output_format=LabelReading,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": resized_media_type,
                                "data": encoded,
                            },
                        },
                        {"type": "text", "text": USER_PROMPT},
                    ],
                }
            ],
        )
    except anthropic.APIConnectionError as exc:
        raise ReadError("Couldn't reach the AI service") from exc
    except anthropic.RateLimitError as exc:
        raise ReadError("AI service is busy, try again") from exc
    except anthropic.APIStatusError as exc:
        if exc.status_code >= 500:
            raise ReadError("AI service is busy, try again") from exc
        raise ReadError("The AI service rejected this request") from exc
    except anthropic.APIError as exc:
        raise ReadError("Couldn't reach the AI service") from exc

    elapsed = time.monotonic() - start
    logger.info("read_label: model call took %.2fs", elapsed)

    if response.stop_reason == "refusal":
        raise ReadError("The AI service declined to read this image")
    if response.stop_reason == "max_tokens":
        raise ReadError("The AI response was cut off before it finished")

    try:
        return response.parsed_output
    except Exception as exc:
        raise ReadError("Couldn't parse the AI service's response") from exc
