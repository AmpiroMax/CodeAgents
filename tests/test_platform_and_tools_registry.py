from __future__ import annotations

from pathlib import Path

import pytest

from codeagents.permissions import Permission
from codeagents.platform.documents import ExtractedDocument, NoopDocumentExtractor
from codeagents.platform.indexing import SqliteCodeIndex
from codeagents.tools import ToolRegistry, ToolSpec


def test_sqlite_code_index_workspace_root(tmp_path: Path) -> None:
    idx = SqliteCodeIndex(tmp_path)
    assert idx.workspace_root() == tmp_path.resolve()


def test_noop_document_extractor_returns_stub(tmp_path: Path) -> None:
    p = tmp_path / "f.pdf"
    p.write_bytes(b"%PDF-1.4")
    ext = NoopDocumentExtractor()
    doc = ext.extract(p, hint="application/pdf")
    assert isinstance(doc, ExtractedDocument)
    assert doc.source_path == str(p)
    assert doc.meta.get("status") == "not_implemented"


def test_tool_registry_unregister() -> None:
    reg = ToolRegistry()
    reg.register(
        ToolSpec(name="t1", kind="native", permission=Permission.READ_ONLY, description="x"),
        handler=lambda _a: {"ok": True},
    )
    assert reg.handler("t1")({}) == {"ok": True}
    reg.unregister("t1")
    with pytest.raises(ValueError, match="no local handler"):
        reg.handler("t1")


def test_tool_spec_mcp_schema_merge_on_reregister() -> None:
    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="mcp.x.t",
            kind="mcp",
            permission=Permission.READ_ONLY,
            description="first",
            mcp_input_schema={"type": "object", "properties": {"a": {"type": "string"}}},
        ),
        handler=lambda _a: {},
    )
    reg.register(
        ToolSpec(
            name="mcp.x.t",
            kind="mcp",
            permission=Permission.READ_ONLY,
            description="second",
            params=(),
        ),
        handler=lambda _a: {"b": 2},
    )
    spec = reg.get("mcp.x.t")
    assert spec.description == "first" or spec.description == "second"
    assert spec.mcp_input_schema is not None
    assert reg.handler("mcp.x.t")({}) == {"b": 2}
