from __future__ import annotations

import base64

import pytest

from codeagents.core.chat_attachments import save_chat_base64_upload


def test_save_chat_base64_upload_writes_file(tmp_path) -> None:
    payload = base64.b64encode(b"hello").decode("ascii")
    out = save_chat_base64_upload(
        tmp_path,
        filename="note.txt",
        content_base64=payload,
        subdir="uploads",
    )
    p = tmp_path / ".codeagents" / "uploads" / "note.txt"
    assert p.read_bytes() == b"hello"
    assert out["bytes"] == 5
    assert out["saved"] == str(p.relative_to(tmp_path))


def test_save_chat_base64_rejects_bad_subdir(tmp_path) -> None:
    with pytest.raises(ValueError, match="subdir"):
        save_chat_base64_upload(
            tmp_path,
            filename="x.txt",
            content_base64=base64.b64encode(b"x").decode(),
            subdir="a/b",
        )


def test_save_chat_base64_rejects_bad_filename(tmp_path) -> None:
    with pytest.raises(ValueError, match="filename"):
        save_chat_base64_upload(
            tmp_path,
            filename="..",
            content_base64=base64.b64encode(b"x").decode(),
        )


def test_save_chat_base64_rejects_non_string_content(tmp_path) -> None:
    with pytest.raises(ValueError, match="content_base64"):
        save_chat_base64_upload(
            tmp_path,
            filename="x.txt",
            content_base64=123,  # type: ignore[arg-type]
        )
