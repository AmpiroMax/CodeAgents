from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class WorkspaceError(ValueError):
    pass


@dataclass(frozen=True)
class Workspace:
    root: Path

    @classmethod
    def from_path(cls, path: Path | str) -> "Workspace":
        root = Path(path).resolve()
        if not root.exists():
            raise WorkspaceError(f"Workspace does not exist: {root}")
        if not root.is_dir():
            raise WorkspaceError(f"Workspace is not a directory: {root}")
        return cls(root=root)

    def resolve_inside(self, path: Path | str) -> Path:
        candidate = (self.root / path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError(f"Path escapes workspace: {path}") from exc
        return candidate
