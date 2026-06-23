"""Source abstraction and format detection for the ingestion pipeline.

A *source* is a single input file plus its intended semantic tagging. This
module provides the value object and format detection so that later stages
(text extraction, relation extraction, bundling) can operate on a uniform
interface.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..synaptic.fiber import Fiber, normalize_fiber

__all__ = [
    "SourceFormat",
    "Source",
    "detect_format",
    "load_source",
    "IngestError",
]


class SourceFormat(str, Enum):
    """Supported input formats."""

    PDF = "pdf"
    HTML = "html"
    JSON = "json"
    TXT = "txt"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Source:
    """A single ingestible source with semantic tagging.

    Attributes:
        path: Filesystem path to the source file.
        format: Detected or declared format.
        fiber: Canonical fiber enum value (e.g. Fiber.DATA).
        domains: Tuple of normalised domain tags (e.g. "data-governance").
        asset_cid: Optional content identifier; if omitted, derived from path.
        originator: Entity responsible for the source assertion.
        metadata: Free-form metadata (title, url, licence, etc.).
    """

    path: Path
    format: SourceFormat
    fiber: Fiber
    domains: tuple[str, ...]
    asset_cid: str
    originator: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.asset_cid:
            object.__setattr__(
                self, "asset_cid", f"source:{self.path.resolve().as_posix()}"
            )


class IngestError(ValueError):
    """Raised when a source cannot be loaded or validated."""


# Extension → format mapping. MIME detection is a fallback.
_EXTENSION_FORMAT: dict[str, SourceFormat] = {
    ".pdf": SourceFormat.PDF,
    ".html": SourceFormat.HTML,
    ".htm": SourceFormat.HTML,
    ".json": SourceFormat.JSON,
    ".txt": SourceFormat.TXT,
    ".md": SourceFormat.TXT,
}


# MIME type prefixes → format.
_MIME_FORMAT: dict[str, SourceFormat] = {
    "application/pdf": SourceFormat.PDF,
    "text/html": SourceFormat.HTML,
    "application/json": SourceFormat.JSON,
    "text/plain": SourceFormat.TXT,
    "text/markdown": SourceFormat.TXT,
}


def detect_format(path: str | Path) -> SourceFormat:
    """Detect the source format from extension and MIME type."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in _EXTENSION_FORMAT:
        return _EXTENSION_FORMAT[ext]

    mime, _ = mimetypes.guess_type(str(p))
    if mime:
        for prefix, fmt in _MIME_FORMAT.items():
            if mime == prefix or mime.startswith(prefix + ";"):
                return fmt

    return SourceFormat.UNKNOWN


def load_source(
    path: str | Path,
    fiber: str | Fiber,
    domains: list[str] | tuple[str, ...] | None = None,
    *,
    asset_cid: str | None = None,
    originator: str = "knitweb-ingest",
    metadata: dict[str, Any] | None = None,
) -> Source:
    """Create and validate a Source value object.

    Args:
        path: Path to the source file.
        fiber: Fiber name or Fiber enum member.
        domains: Optional domain tags; normalised to lower-case hyphenated form.
        asset_cid: Optional CID; derived from resolved path if omitted.
        originator: Entity asserting this source.
        metadata: Optional metadata dict.

    Raises:
        IngestError: if the path does not exist, the fiber is unknown, or the
            format is unsupported.
    """
    p = Path(path)
    if not p.exists():
        raise IngestError(f"source path does not exist: {p}")
    if not p.is_file():
        raise IngestError(f"source path is not a file: {p}")

    fmt = detect_format(p)
    if fmt is SourceFormat.UNKNOWN:
        raise IngestError(f"unsupported source format: {p}")

    try:
        fiber_enum = normalize_fiber(fiber)
    except ValueError as exc:
        raise IngestError(str(exc)) from exc

    normalised_domains = tuple(
        "-".join(part for part in d.strip().lower().split() if part)
        for d in (domains or ())
        if d and d.strip()
    )

    return Source(
        path=p,
        format=fmt,
        fiber=fiber_enum,
        domains=normalised_domains,
        asset_cid=asset_cid or f"source:{p.resolve().as_posix()}",
        originator=originator,
        metadata=dict(metadata or {}),
    )
