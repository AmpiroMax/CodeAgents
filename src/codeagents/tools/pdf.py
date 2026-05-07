"""PDF reading tool.

Exposes a single ``read_pdf`` tool that extracts text from either a
local PDF file (resolved against the workspace) or a remote URL
(fetched via ``httpx``). The agent receives plain text plus a small
amount of structural metadata (page count, page range returned).

Implementation notes:
  * Pure-Python ``pypdf`` keeps the dependency footprint small. For
    image-only PDFs the extractor returns empty strings per page; we
    surface that as a hint so the model doesn't loop forever.
  * URL downloads are streamed into a temp file so we never need the
    whole binary in memory twice. The temp file is removed eagerly.
  * Output is capped by ``max_chars`` (default 20k) to protect the
    context window. The cap is applied AFTER per-page assembly so
    earlier pages are preferred over later ones.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any
from urllib.parse import urlparse

from codeagents.permissions import Permission
from codeagents.tools import ParamSpec, ToolRegistry, ToolSpec
from codeagents.workspace import Workspace


_PDF_SIGNATURE = b"%PDF"


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"}


def _download_pdf(url: str, *, timeout: int) -> str:
    import httpx

    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            fd, tmp_path = tempfile.mkstemp(suffix=".pdf", prefix="ca_pdf_")
            try:
                with os.fdopen(fd, "wb") as fh:
                    for chunk in response.iter_bytes():
                        if chunk:
                            fh.write(chunk)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
    return tmp_path


def _extract_pages(path: str, *, page_start: int, page_end: int | None) -> tuple[list[str], int, dict[str, Any]]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    total_pages = len(reader.pages)
    if total_pages == 0:
        return [], 0, {}

    start_idx = max(page_start - 1, 0)
    if page_end is None or page_end <= 0:
        end_idx = total_pages
    else:
        end_idx = min(page_end, total_pages)
    if start_idx >= end_idx:
        return [], total_pages, {}

    pages: list[str] = []
    for i in range(start_idx, end_idx):
        try:
            pages.append(reader.pages[i].extract_text() or "")
        except Exception as exc:  # bad font, encrypted page, etc.
            pages.append(f"[error extracting page {i + 1}: {exc}]")

    info: dict[str, Any] = {}
    try:
        meta = reader.metadata or {}
        if meta:
            info = {
                k.lstrip("/"): str(v)
                for k, v in meta.items()
                if v is not None
            }
    except Exception:
        info = {}
    return pages, total_pages, info


def read_pdf(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    src_raw = args.get("path") or args.get("url")
    if not src_raw or not isinstance(src_raw, str):
        return {"error": "missing argument: pass either path=<local pdf> or url=<https://…>"}
    src = src_raw.strip()

    page_start = max(int(args.get("page_start", 1) or 1), 1)
    page_end_raw = args.get("page_end")
    page_end = int(page_end_raw) if page_end_raw not in (None, "") else None
    max_chars = int(args.get("max_chars", 20_000) or 20_000)
    timeout = int(args.get("timeout", 45) or 45)

    tmp_to_cleanup: str | None = None
    try:
        if _is_url(src):
            local_path = _download_pdf(src, timeout=timeout)
            tmp_to_cleanup = local_path
            display_source = src
        else:
            resolved = workspace.resolve_for_read(src)
            if not resolved.exists():
                return {"error": f"file not found: {workspace.display_path(resolved)}"}
            if not resolved.is_file():
                return {"error": f"not a file: {workspace.display_path(resolved)}"}
            local_path = str(resolved)
            display_source = workspace.display_path(resolved)

        # Cheap signature check so we fail loudly for an HTML error page or
        # plain text accidentally fed to read_pdf.
        try:
            with open(local_path, "rb") as fh:
                head = fh.read(8)
            if not head.startswith(_PDF_SIGNATURE):
                return {
                    "error": "not a PDF (bad signature)",
                    "source": display_source,
                    "head_hex": head.hex(),
                }
        except OSError as exc:
            return {"error": f"could not open downloaded file: {exc}", "source": display_source}

        pages, total_pages, info = _extract_pages(
            local_path,
            page_start=page_start,
            page_end=page_end,
        )

        joined: list[str] = []
        used_pages = 0
        approx = 0
        for idx, text in enumerate(pages, start=page_start):
            block = f"--- Page {idx} ---\n{text.rstrip()}\n"
            if approx + len(block) > max_chars and joined:
                break
            joined.append(block)
            approx += len(block)
            used_pages += 1
        body = "".join(joined)
        truncated = used_pages < len(pages)
        empty = sum(1 for p in pages if not p.strip())
        result: dict[str, Any] = {
            "source": display_source,
            "total_pages": total_pages,
            "returned_pages": used_pages,
            "page_start": page_start,
            "page_end": page_start + used_pages - 1 if used_pages else page_start - 1,
            "truncated": truncated,
            "content": body,
            "info": info,
        }
        if pages and empty == len(pages):
            result["hint"] = (
                "all extracted pages are empty — the PDF is likely image-only "
                "and needs OCR (not implemented)."
            )
        return result
    except Exception as exc:
        return {"error": f"read_pdf failed: {exc}", "source": src}
    finally:
        if tmp_to_cleanup:
            try:
                os.unlink(tmp_to_cleanup)
            except OSError:
                pass


def register_pdf_tools(registry: ToolRegistry, workspace: Workspace) -> None:
    registry.register(
        ToolSpec(
            name="read_pdf",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Extract plain text from a PDF (local file or HTTPS URL, e.g. "
                "an arxiv.org/pdf/<id> link). Returns per-page text joined with "
                "'--- Page N ---' separators, plus total_pages, returned_pages, "
                "and a truncated flag. Pass page_start/page_end to read a "
                "slice; max_chars caps the body (default 20k chars). "
                "Image-only PDFs return empty content with a hint — OCR is not "
                "supported."
            ),
            params=(
                ParamSpec(
                    name="path",
                    description="Local path inside the workspace. Mutually exclusive with url.",
                    required=False,
                ),
                ParamSpec(
                    name="url",
                    description="HTTPS URL to a PDF (e.g. https://arxiv.org/pdf/2506.12594).",
                    required=False,
                ),
                ParamSpec(
                    name="page_start",
                    type="integer",
                    description="1-based first page to extract (default 1).",
                    required=False,
                ),
                ParamSpec(
                    name="page_end",
                    type="integer",
                    description="Inclusive last page to extract (default: last page).",
                    required=False,
                ),
                ParamSpec(
                    name="max_chars",
                    type="integer",
                    description="Cap on total characters returned (default 20000).",
                    required=False,
                ),
                ParamSpec(
                    name="timeout",
                    type="integer",
                    description="HTTP timeout in seconds when url is used (default 45).",
                    required=False,
                ),
            ),
        ),
        handler=lambda args: read_pdf(workspace, args),
    )


__all__ = ["read_pdf", "register_pdf_tools"]
