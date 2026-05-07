from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from codeagents.config import PROJECT_ROOT
from codeagents.observability._jsonl import append_line
from codeagents.schemas import InferenceRequest, InferenceResponse


class InferenceLogEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    request: InferenceRequest
    response: InferenceResponse | None = None
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class InferenceLogger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or PROJECT_ROOT / ".codeagents" / "inference.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, entry: InferenceLogEntry) -> None:
        append_line(self.path, entry.model_dump_json(exclude_none=True))

    def record_success(
        self,
        *,
        request: InferenceRequest,
        response: InferenceResponse,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.record(InferenceLogEntry(request=request, response=response, meta=meta or {}))

    def record_error(
        self,
        *,
        request: InferenceRequest,
        error: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.record(InferenceLogEntry(request=request, error=error, meta=meta or {}))

    def tail(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines[-limit:]]
