"""Offline tests for labelcheck.reader. No network calls are made."""

import base64
import io
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from labelcheck import reader
from labelcheck.models import LabelReading


def _make_image_bytes(width: int, height: int, fmt: str = "PNG") -> bytes:
    img = Image.new("RGB", (width, height), color=(10, 20, 30))
    buffer = io.BytesIO()
    img.save(buffer, format=fmt)
    return buffer.getvalue()


def _patch_client(monkeypatch, parse_return):
    fake_parse = MagicMock(return_value=parse_return)
    fake_client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=fake_parse)))
    monkeypatch.setattr(reader, "_get_client", lambda: fake_client)
    return fake_parse


def _parsed_response(stop_reason="end_turn", parsed_output=None):
    return SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed_output)


def test_large_image_is_downscaled_and_base64_encoded(monkeypatch):
    big_bytes = _make_image_bytes(3000, 1500)
    reading = LabelReading(brand_name="Test Brand")
    fake_parse = _patch_client(monkeypatch, _parsed_response(parsed_output=reading))

    reader.read_label(big_bytes, "image/png")

    kwargs = fake_parse.call_args.kwargs
    assert kwargs["model"] == reader.MODEL

    content = kwargs["messages"][0]["content"]
    image_block = next(b for b in content if b["type"] == "image")
    assert image_block["source"]["type"] == "base64"

    decoded = base64.standard_b64decode(image_block["source"]["data"])
    with Image.open(io.BytesIO(decoded)) as img:
        assert max(img.size) <= reader.MAX_LONG_SIDE


def test_small_image_is_not_resized_but_is_base64(monkeypatch):
    small_bytes = _make_image_bytes(200, 100)
    reading = LabelReading(brand_name="Test Brand")
    fake_parse = _patch_client(monkeypatch, _parsed_response(parsed_output=reading))

    reader.read_label(small_bytes, "image/png")

    kwargs = fake_parse.call_args.kwargs
    content = kwargs["messages"][0]["content"]
    image_block = next(b for b in content if b["type"] == "image")
    decoded = base64.standard_b64decode(image_block["source"]["data"])
    assert decoded == small_bytes


def test_refusal_raises_read_error(monkeypatch):
    small_bytes = _make_image_bytes(200, 100)
    _patch_client(monkeypatch, _parsed_response(stop_reason="refusal"))

    with pytest.raises(reader.ReadError):
        reader.read_label(small_bytes, "image/png")


def test_max_tokens_raises_read_error(monkeypatch):
    small_bytes = _make_image_bytes(200, 100)
    _patch_client(monkeypatch, _parsed_response(stop_reason="max_tokens"))

    with pytest.raises(reader.ReadError):
        reader.read_label(small_bytes, "image/png")


def test_successful_parse_returns_label_reading(monkeypatch):
    small_bytes = _make_image_bytes(200, 100)
    reading = LabelReading(
        brand_name="Old Crow",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="40% Alc./Vol.",
    )
    _patch_client(monkeypatch, _parsed_response(parsed_output=reading))

    result = reader.read_label(small_bytes, "image/png")

    assert isinstance(result, LabelReading)
    assert result.brand_name == "Old Crow"
    assert result.class_type == "Kentucky Straight Bourbon Whiskey"


def test_unsupported_media_type_raises_read_error():
    with pytest.raises(reader.ReadError):
        reader.read_label(b"not-an-image", "image/bmp")
