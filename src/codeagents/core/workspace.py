from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List


class WorkspaceError(ValueError):
    pass


@dataclass
class Workspace:
    """Mutable workspace handle.

    `root` is the trust boundary for write operations. `cwd` is the agent's
    current working directory and may move freely (including outside `root`).
    When `cwd` is outside `root`, the workspace is in implicit read-only mode:
    write tools refuse because their resolved paths fall outside `root`.
    """

    root: Path
    cwd: Path = field(default=None)  # type: ignore[assignment]
    on_root_change: List[Callable[["Workspace"], None]] = field(default_factory=list)
    # Active chat id (mutated per-turn by AgentCore). Plan tools read this to
    # scope writes to ``<chats_dir>/<chat_id>/plans/``. Empty string means
    # "no chat context yet" — plans then land in the orphan bucket.
    chat_id: str = ""

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        if self.cwd is None:
            self.cwd = self.root
        else:
            self.cwd = Path(self.cwd).resolve()

    @classmethod
    def from_path(cls, path: Path | str) -> "Workspace":
        root = Path(path).resolve()
        if not root.exists():
            raise WorkspaceError(f"Workspace does not exist: {root}")
        if not root.is_dir():
            raise WorkspaceError(f"Workspace is not a directory: {root}")
        return cls(root=root, cwd=root)

    def is_inside_root(self, path: Path | str | None = None) -> bool:
        candidate = Path(path).resolve() if path is not None else self.cwd
        try:
            candidate.relative_to(self.root)
            return True
        except ValueError:
            return False

    @property
    def read_only(self) -> bool:
        """True when the agent has cd-ed outside its workspace root."""
        return not self.is_inside_root(self.cwd)

    def resolve_for_read(self, path: Path | str) -> Path:
        """Resolve a path for read operations.

        Relative paths resolve against `cwd`. Absolute paths are accepted as-is.
        No boundary check — read tools may peek anywhere on disk.
        """
        raw = Path(path)
        candidate = raw if raw.is_absolute() else (self.cwd / raw)
        return candidate.resolve()

    def resolve_inside(self, path: Path | str) -> Path:
        """Resolve a path that must stay inside `root` (for write/safe tools).

        Relative paths resolve against `cwd`. Raises WorkspaceError if the
        result escapes `root`.
        """
        raw = Path(path)
        candidate = raw if raw.is_absolute() else (self.cwd / raw)
        candidate = candidate.resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError(f"Path escapes workspace: {path}") from exc
        return candidate

    def display_path(self, path: Path) -> str:
        """Render a path relative to root when possible, else absolute."""
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def change_cwd(self, path: Path | str) -> Path:
        """Move the current working directory anywhere on disk.

        Existence and directory checks are enforced. Returns the resolved cwd.
        """
        target = self.resolve_for_read(path)
        if not target.exists():
            raise WorkspaceError(f"Directory not found: {path}")
        if not target.is_dir():
            raise WorkspaceError(f"Not a directory: {path}")
        self.cwd = target
        return target

    def change_root(self, path: Path | str) -> Path:
        """Switch the workspace root. Creates `.codeagents/` skeleton.

        cwd is reset to the new root. Existing tool registrations keep working
        because they captured the same `Workspace` instance by reference.
        """
        target = Path(path).expanduser().resolve()
        if target.exists() and not target.is_dir():
            raise WorkspaceError(f"Not a directory: {path}")
        target.mkdir(parents=True, exist_ok=True)
        (target / ".codeagents").mkdir(parents=True, exist_ok=True)
        self.root = target
        self.cwd = target
        for callback in list(self.on_root_change):
            try:
                callback(self)
            except Exception:
                continue
        return target
