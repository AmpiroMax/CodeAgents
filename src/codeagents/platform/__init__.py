"""Pluggable backends and extractors (indexing, documents) for future phases."""

from codeagents.platform.documents import DocumentExtractor, ExtractedDocument
from codeagents.platform.indexing import CodeIndexBackend

__all__ = ["CodeIndexBackend", "DocumentExtractor", "ExtractedDocument"]
