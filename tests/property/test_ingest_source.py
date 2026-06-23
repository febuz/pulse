"""Property tests for the ingestion source abstraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from knitweb.ingest.source import (
    SourceFormat,
    detect_format,
    load_source,
    IngestError,
)
from knitweb.synaptic.fiber import Fiber


@pytest.mark.property
def test_detect_format_by_extension(tmp_path: Path):
    assert detect_format(tmp_path / "paper.pdf") is SourceFormat.PDF
    assert detect_format(tmp_path / "page.html") is SourceFormat.HTML
    assert detect_format(tmp_path / "data.json") is SourceFormat.JSON
    assert detect_format(tmp_path / "notes.txt") is SourceFormat.TXT
    assert detect_format(tmp_path / "readme.md") is SourceFormat.TXT


@pytest.mark.property
def test_detect_format_unknown(tmp_path: Path):
    assert detect_format(tmp_path / "archive.bin") is SourceFormat.UNKNOWN


@pytest.mark.property
def test_load_source_creates_source(tmp_path: Path):
    p = tmp_path / "test.txt"
    p.write_text("hello")
    src = load_source(p, "data", ["data governance", "quality"])
    assert src.format is SourceFormat.TXT
    assert src.fiber is Fiber.DATA
    assert src.domains == ("data-governance", "quality")
    assert src.asset_cid.startswith("source:")
    assert "test.txt" in src.asset_cid


@pytest.mark.property
def test_load_source_accepts_fiber_enum(tmp_path: Path):
    p = tmp_path / "test.json"
    p.write_text("{}")
    src = load_source(p, Fiber.CHEM, ["organic"])
    assert src.fiber is Fiber.CHEM
    assert src.domains == ("organic",)


@pytest.mark.property
def test_load_source_rejects_missing_path():
    with pytest.raises(IngestError):
        load_source("/nonexistent/file.pdf", "data")


@pytest.mark.property
def test_load_source_rejects_directory(tmp_path: Path):
    with pytest.raises(IngestError):
        load_source(tmp_path, "data")


@pytest.mark.property
def test_load_source_rejects_unknown_fiber(tmp_path: Path):
    p = tmp_path / "test.txt"
    p.write_text("hello")
    with pytest.raises(IngestError):
        load_source(p, "magic")


@pytest.mark.property
def test_load_source_rejects_unknown_format(tmp_path: Path):
    p = tmp_path / "data.bin"
    p.write_bytes(b"\x00\x01\x02")
    with pytest.raises(IngestError):
        load_source(p, "data")


@pytest.mark.property
def test_load_source_accepts_asset_cid_and_originator(tmp_path: Path):
    p = tmp_path / "test.txt"
    p.write_text("hello")
    src = load_source(
        p,
        "academic",
        ["math"],
        asset_cid="urn:arxiv:1234",
        originator="arxiv",
    )
    assert src.asset_cid == "urn:arxiv:1234"
    assert src.originator == "arxiv"
