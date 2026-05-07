#!/usr/bin/env python3
"""Replace ``from codeagents.X`` / ``import codeagents.X`` across the tree.

Usage::

    python scripts/refactor_codemod.py old_module new_module [old new ...]

Operates on every ``.py`` file under ``src/codeagents/`` and ``tests/``.
Each pair rewrites four shapes:

* ``from codeagents.OLD import ...`` -> ``from codeagents.NEW import ...``
* ``from codeagents.OLD.SUB import ...`` -> ``from codeagents.NEW.SUB import ...``
* ``import codeagents.OLD as ...`` -> ``import codeagents.NEW as ...``
* ``import codeagents.OLD`` (line-end or before whitespace/comma) -> ``import codeagents.NEW``

This is a one-shot tool used by the honest-refactor plan; it is not part
of the runtime package. After running, re-grep the tree for the old
module name to confirm zero residue.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOTS = ("src/codeagents", "tests")


def _patterns(old: str, new: str) -> list[tuple[re.Pattern[str], str]]:
    old_re = re.escape(old)
    return [
        (re.compile(rf"\bfrom\s+codeagents\.{old_re}(\.[\w\.]+)?\b"), rf"from codeagents.{new}\1"),
        (re.compile(rf"\bimport\s+codeagents\.{old_re}(\.[\w\.]+)?\b"), rf"import codeagents.{new}\1"),
    ]


def main(argv: list[str]) -> int:
    if len(argv) < 2 or len(argv) % 2:
        print(__doc__)
        return 2
    pairs = list(zip(argv[0::2], argv[1::2]))
    project = Path(__file__).resolve().parents[1]
    patterns: list[tuple[re.Pattern[str], str]] = []
    for old, new in pairs:
        patterns.extend(_patterns(old, new))

    changed = 0
    for root in ROOTS:
        base = project / root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            new_text = text
            for pattern, repl in patterns:
                new_text = pattern.sub(repl, new_text)
            if new_text != text:
                path.write_text(new_text, encoding="utf-8")
                changed += 1
    print(f"codemod: rewrote {changed} files for {len(pairs)} mappings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
