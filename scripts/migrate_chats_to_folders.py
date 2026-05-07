#!/usr/bin/env python3
"""Migrate v3.0 flat chat / plan files into the v3.1 per-chat folder layout.

Old layout:
    <chats_dir>/<chat_id>.json                ← chat
    <chats_dir>/../plans/<plan_id>.json[+md]  ← plans (flat, separate folder)

New layout:
    <chats_dir>/<chat_id>/chat.json
    <chats_dir>/<chat_id>/plans/<plan_id>.json (+ .md)
    <chats_dir>/_orphans/plans/<plan_id>.json  ← plans whose chat_id is empty
                                                or doesn't exist any more.

Behaviour:
- Idempotent: re-running on an already-migrated tree is a no-op.
- Dry-run by default. Pass ``--apply`` to actually move files.
- Honours ``CODEAGENTS_CHATS_DIR`` (same env var ChatStore uses).
- Logs every move so you can audit what happened.

Usage:
    ./scripts/migrate_chats_to_folders.py            # dry-run, prints plan
    ./scripts/migrate_chats_to_folders.py --apply    # actually move files
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# We deliberately avoid importing ``codeagents`` so this script can be run with
# any system Python, even outside the project venv. The chats-dir resolution
# is duplicated from ``codeagents.chat_store.default_chats_dir`` — keep them in
# sync if that logic ever changes.
HARDCODED_CHATS_DIR = Path("/Users/ampiro/programs/CodeAgents/.codeagents/chats")


def default_chats_dir() -> Path:
    raw = os.environ.get("CODEAGENTS_CHATS_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return HARDCODED_CHATS_DIR.resolve()


def _legacy_plans_dir(chats_dir: Path) -> Path:
    """v3.0 default lived at ``<.codeagents>/plans`` — sibling of chats/."""
    return chats_dir.parent / "plans"


def plan_chat_id(plan_path: Path) -> str:
    try:
        raw = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(raw.get("chat_id") or "").strip()


def migrate(chats_dir: Path, *, apply: bool) -> int:
    moves: list[tuple[Path, Path]] = []

    # --- Chats: <chats_dir>/<id>.json → <chats_dir>/<id>/chat.json ---------
    for entry in sorted(chats_dir.iterdir()) if chats_dir.exists() else []:
        if entry.is_file() and entry.suffix == ".json":
            chat_id = entry.stem
            target_dir = chats_dir / chat_id
            target = target_dir / "chat.json"
            if target.exists():
                # Already migrated — drop the stray flat copy.
                moves.append((entry, target_dir / "_obsolete_flat_copy.json"))
                continue
            moves.append((entry, target))

    # --- Plans: legacy <.codeagents>/plans/* → per-chat plans/ -------------
    legacy_plans = _legacy_plans_dir(chats_dir)
    if legacy_plans.is_dir():
        for plan_file in sorted(legacy_plans.iterdir()):
            if not plan_file.is_file():
                continue
            if plan_file.suffix not in {".json", ".md"}:
                continue
            sibling = (
                plan_file.with_suffix(".json") if plan_file.suffix == ".md" else plan_file
            )
            chat_id = plan_chat_id(sibling)
            bucket_chat = chat_id if chat_id else "_orphans"
            # If the chat_id points at a chat that doesn't (and won't) exist,
            # still send the plan to its own bucket — never silently lose data.
            target_dir = chats_dir / bucket_chat / "plans"
            target = target_dir / plan_file.name
            if target == plan_file:
                continue
            moves.append((plan_file, target))

    if not moves:
        print("Nothing to migrate — tree is already in the v3.1 layout.")
        return 0

    print(f"{'APPLY' if apply else 'DRY-RUN'}: planning {len(moves)} move(s).")
    for src, dst in moves:
        rel_src = src.relative_to(chats_dir.parent)
        rel_dst = dst.relative_to(chats_dir.parent)
        print(f"  {rel_src}  →  {rel_dst}")
        if not apply:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            # Should only happen for the "stray flat copy" branch above.
            dst = dst.with_name(f"{dst.stem}.orphan{dst.suffix}")
        try:
            src.replace(dst)
        except OSError:
            # Cross-device fallback.
            shutil.copy2(src, dst)
            src.unlink(missing_ok=True)

    if apply:
        # Remove the now-empty legacy plans dir if we drained it completely.
        if legacy_plans.is_dir() and not any(legacy_plans.iterdir()):
            legacy_plans.rmdir()
            print(f"Removed empty {legacy_plans.relative_to(chats_dir.parent)}/")

    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the moves (otherwise dry-run only).",
    )
    p.add_argument(
        "--chats-dir",
        type=Path,
        default=None,
        help="Override the chats directory (defaults to the same one ChatStore uses).",
    )
    args = p.parse_args(argv)
    chats_dir = (args.chats_dir or default_chats_dir()).expanduser().resolve()
    print(f"chats_dir = {chats_dir}")
    return migrate(chats_dir, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
