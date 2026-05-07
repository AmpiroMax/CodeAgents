from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from codeagents.core.config import PROJECT_ROOT
from codeagents.observability._jsonl import append_line


class ServiceRequestLogEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    service: str
    path: str
    method: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: int | None = None
    error: str | None = None
    elapsed_ms: float | None = None


class ServiceRequestLogger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or PROJECT_ROOT / ".codeagents" / "service_requests.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, entry: ServiceRequestLogEntry) -> None:
        append_line(self.path, entry.model_dump_json(exclude_none=True))

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines[-limit:]]
