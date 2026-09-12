"""Tests for rlm.vision — view_image REPL function."""

import base64
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest

from rlm.vision import make_view_image


class FakeAgent:
    """Minimal agent stub with _queued_images."""

    def __init__(self):
        self._queued_images: list[tuple[str, str, str]] = []


def _make_tiny_png(path: Path) -> None:
    """Create a minimal valid 1x1 red PNG file."""
    # PNG signature
    data = b"\x89PNG\r\n\x1a\n"
    # IHDR chunk: 1x1, 8-bit RGB
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
    data += struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
    # IDAT chunk: single red pixel
    raw = b"\x00\xff\x00\x00"  # filter byte + RGB
    compressed = zlib.compress(raw)
    idat_crc = zlib.crc32(b"IDAT" + compressed) & 0xFFFFFFFF
    data += struct.pack(">I", len(compressed)) + b"IDAT" + compressed + struct.pack(">I", idat_crc)
    # IEND chunk
    iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
    data += struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
    path.write_bytes(data)


class TestMakeViewImage:
    def test_returns_callable(self):
        agent = FakeAgent()
        vi = make_view_image(agent)
        assert callable(vi)

    def test_nonexistent_file(self):
        agent = FakeAgent()
        vi = make_view_image(agent)
        result = vi("/nonexistent/image.jpg")
        assert "Error" in result
        assert "not found" in result
        assert len(agent._queued_images) == 0

    def test_non_image_extension(self, tmp_path):
        agent = FakeAgent()
        vi = make_view_image(agent)
        f = tmp_path / "somefile.txt"
        f.write_text("not an image")
        result = vi(str(f))
        assert "Error" in result
        assert "unsupported" in result
        assert len(agent._queued_images) == 0

    def test_directory_not_file(self, tmp_path):
        agent = FakeAgent()
        vi = make_view_image(agent)
        d = tmp_path / "mydir"
        d.mkdir()
        result = vi(str(d))
        assert "Error" in result
        assert len(agent._queued_images) == 0


class TestViewImage:
    def test_queues_png(self, tmp_path):
        agent = FakeAgent()
        vi = make_view_image(agent)
        img = tmp_path / "test.png"
        _make_tiny_png(img)
        result = vi(str(img), "A test image")
        assert "Queued" not in result
        assert "test.png" in result
        assert len(agent._queued_images) == 1
        data_uri, mime, desc = agent._queued_images[0]
        assert data_uri.startswith("data:image/png;base64,")
        assert mime == "image/png"
        assert desc == "A test image"

    def test_default_description_is_filename(self, tmp_path):
        agent = FakeAgent()
        vi = make_view_image(agent)
        img = tmp_path / "photo.png"
        _make_tiny_png(img)
        vi(str(img))
        assert agent._queued_images[0][2] == "photo.png"

    def test_multiple_images(self, tmp_path):
        agent = FakeAgent()
        vi = make_view_image(agent)
        for i in range(3):
            img = tmp_path / f"img{i}.png"
            _make_tiny_png(img)
            vi(str(img))
        assert len(agent._queued_images) == 3
        result = vi(str(tmp_path / "img0.png"))
        assert len(agent._queued_images) == 4
        assert "img0.png" in result

    def test_data_uri_decodes_to_valid_png(self, tmp_path):
        agent = FakeAgent()
        vi = make_view_image(agent)
        img = tmp_path / "test.png"
        original = _make_tiny_png(img)
        original_bytes = img.read_bytes()
        vi(str(img))
        data_uri = agent._queued_images[0][0]
        b64_part = data_uri.split("base64,")[1]
        decoded = base64.b64decode(b64_part)
        assert decoded == original_bytes

    def test_confirmation_message_format(self, tmp_path):
        agent = FakeAgent()
        vi = make_view_image(agent)
        img = tmp_path / "test.png"
        _make_tiny_png(img)
        result = vi(str(img))
        assert "Queued" not in result
        assert "image/png" in result
        assert "test.png" in result
