from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ExtractedDocument(BaseModel):
    """Normalized output for PDF / image / mixed pipelines."""

    source_path: str
    mime_type: str = ""
    text: str = ""
    pages: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class DocumentExtractor(Protocol):
    """Future PDF/OCR/caption pipelines implement this interface."""

    def extract(self, path: Path, *, hint: str | None = None) -> ExtractedDocument: ...


class NoopDocumentExtractor:
    """Placeholder until PDF/vision backends are wired."""

    def extract(self, path: Path, *, hint: str | None = None) -> ExtractedDocument:
        return ExtractedDocument(
            source_path=str(path),
            mime_type=hint or "",
            text="",
            meta={"status": "not_implemented"},
        )
