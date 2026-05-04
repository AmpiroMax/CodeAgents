from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class AuditEvent:
    id: str
    timestamp: str
    session_id: str
    tool_name: str
    permission: str
    arguments: dict[str, Any]
    result_summary: str
    confirmation_required: bool


class AuditLog:
    def __init__(self, path: Path, session_id: str | None = None) -> None:
        self.path = path
        self.session_id = session_id or str(uuid4())
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        tool_name: str,
        permission: str,
        arguments: dict[str, Any],
        result_summary: str,
        confirmation_required: bool,
    ) -> AuditEvent:
        event = AuditEvent(
            id=str(uuid4()),
            timestamp=datetime.now(UTC).isoformat(),
            session_id=self.session_id,
            tool_name=tool_name,
            permission=permission,
            arguments=arguments,
            result_summary=result_summary,
            confirmation_required=confirmation_required,
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        return event
