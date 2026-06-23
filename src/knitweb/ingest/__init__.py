"""Body-of-knowledge ingestion pipeline.

Turns raw sources (PDF, HTML, JSON, TXT) into tagged Fiber synaptic bundles
that feed the agent army corpus and the Lens-powered A/B demo.
"""

from .source import Source, SourceFormat, detect_format, load_source

__all__ = ["Source", "SourceFormat", "detect_format", "load_source"]
