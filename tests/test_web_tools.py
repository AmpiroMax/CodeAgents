from __future__ import annotations

import json
import urllib.error
import base64
import gzip
from pathlib import Path
from typing import Any

from codeagents.agent import AgentCore
from codeagents.tools_native import code as code_tools
from codeagents.tools_native.code import curl, docs_search, web_fetch, web_search
from codeagents.workspace import Workspace


def _workspace(path: Path) -> Workspace:
    return Workspace.from_path(path)


def test_web_fetch_uses_jina_and_cache(tmp_path: Path, monkeypatch: Any) -> None:
    calls: list[str] = []

    def fake_get(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30):
        calls.append(url)
        return 200, "# Example\nFetched content"

    monkeypatch.setattr(code_tools, "_http_get_text", fake_get)
    workspace = _workspace(tmp_path)

    first = web_fetch(workspace, {"url": "https://example.com/docs", "max_chars": 20})
    second = web_fetch(workspace, {"url": "https://example.com/docs", "max_chars": 20})

    assert first["provider"] == "jina"
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["content"] == "# Example\nFetched co"
    assert calls == ["https://r.jina.ai/https://example.com/docs"]
    assert (tmp_path / ".codeagents" / "web_cache.sqlite3").exists()


def test_web_search_parses_searxng_results(tmp_path: Path, monkeypatch: Any) -> None:
    def fake_get(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30):
        payload = {
            "results": [
                {
                    "title": "Python venv",
                    "url": "https://docs.python.org/3/library/venv.html",
                    "content": "Creation of virtual environments.",
                    "engine": "python-docs",
                }
            ]
        }
        return 200, json.dumps(payload)

    monkeypatch.setattr(code_tools, "_http_get_text", fake_get)
    workspace = _workspace(tmp_path)

    result = web_search(workspace, {"query": "python venv", "provider": "searxng", "limit": 5})

    assert result["provider"] == "searxng"
    assert result["results"][0]["title"] == "Python venv"
    assert result["results"][0]["url"].startswith("https://docs.python.org")


def test_web_search_auto_prefers_configured_yandex(tmp_path: Path, monkeypatch: Any) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "local.toml").write_text(
        """
[yandex_search]
api_key = "key"
folder_id = "folder"
""",
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_post(
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
        timeout: int = 30,
        verify_ssl_certs: bool = True,
    ):
        calls.append(url)
        html = '<html><body><a href="https://python.org/">Python</a></body></html>'
        return 200, json.dumps({"rawData": base64.b64encode(html.encode()).decode()})

    monkeypatch.setattr(code_tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(code_tools, "_LOCAL_CONFIG_CACHE", None)
    monkeypatch.setattr(code_tools, "_http_post_text", fake_post)
    workspace = _workspace(tmp_path)

    result = web_search(workspace, {"query": "python latest", "provider": "auto"})

    assert result["provider"] == "yandex"
    assert calls == ["https://searchapi.api.cloud.yandex.net/v2/web/search"]


def test_web_fetch_cleans_direct_html(tmp_path: Path, monkeypatch: Any) -> None:
    html = """
    <html>
      <head><title>Example Docs</title><style>.x{}</style><script>alert(1)</script></head>
      <body>
        <nav>Navigation noise</nav>
        <main><h1>Install</h1><p>Use pip install package.</p>
        <a href="https://example.com/docs">Docs link</a></main>
        <footer>Footer noise</footer>
      </body>
    </html>
    """

    def fake_get(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30):
        return 200, html

    monkeypatch.setattr(code_tools, "_http_get_text", fake_get)
    workspace = _workspace(tmp_path)

    result = web_fetch(
        workspace,
        {"url": "https://example.com", "provider": "direct", "no_cache": True},
    )

    assert result["cleaned"] is True
    assert "Use pip install package." in result["content"]
    assert "Navigation noise" not in result["content"]
    assert "alert(1)" not in result["content"]
    assert result["links"] == [{"text": "Docs link", "url": "https://example.com/docs"}]


def test_curl_returns_clean_text(tmp_path: Path, monkeypatch: Any) -> None:
    class FakeHeaders(dict):
        def get_content_charset(self):
            return "utf-8"

    def fake_request_bytes(
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
        timeout: int = 30,
        verify_ssl_certs: bool = True,
    ):
        assert method == "POST"
        assert headers and headers["Content-Type"] == "application/json"
        assert data == b'{"hello": "world"}'
        return (
            200,
            FakeHeaders({"Content-Type": "text/html; charset=utf-8"}),
            b"<html><body><script>bad()</script><main>Hello curl</main></body></html>",
        )

    monkeypatch.setattr(code_tools, "_http_request_bytes", fake_request_bytes)
    workspace = _workspace(tmp_path)

    result = curl(
        workspace,
        {"url": "https://example.com/api", "method": "POST", "json": {"hello": "world"}},
    )

    assert result["status"] == 200
    assert result["cleaned"] is True
    assert result["content"] == "Hello curl"


def test_curl_downloads_inside_workspace(tmp_path: Path, monkeypatch: Any) -> None:
    class FakeHeaders(dict):
        def get_content_charset(self):
            return None

    def fake_request_bytes(
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
        timeout: int = 30,
        verify_ssl_certs: bool = True,
    ):
        return 200, FakeHeaders({"Content-Type": "application/octet-stream"}), b"downloaded"

    monkeypatch.setattr(code_tools, "_http_request_bytes", fake_request_bytes)
    workspace = _workspace(tmp_path)

    result = curl(
        workspace,
        {"url": "https://example.com/file.bin", "output_path": "downloads/file.bin"},
    )

    assert result["output_path"] == "downloads/file.bin"
    assert (tmp_path / "downloads" / "file.bin").read_bytes() == b"downloaded"


def test_curl_rejects_internal_output_path(tmp_path: Path, monkeypatch: Any) -> None:
    workspace = _workspace(tmp_path)

    try:
        curl(workspace, {"url": "https://example.com", "output_path": ".codeagents/x"})
    except ValueError as exc:
        assert "internal state" in str(exc)
    else:
        raise AssertionError("curl should reject .codeagents output")


def test_http_get_text_decodes_gzip(monkeypatch: Any) -> None:
    html = "<html><body>Hello gzip</body></html>"

    class FakeHeaders(dict):
        def get_content_charset(self):
            return "utf-8"

    class FakeResponse:
        status = 200
        headers = FakeHeaders({"Content-Encoding": "gzip"})

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return gzip.compress(html.encode())

    monkeypatch.setattr(code_tools.urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse())

    status, text = code_tools._http_get_text("https://example.com")

    assert status == 200
    assert text == html


def test_web_fetch_auto_falls_back_to_direct(tmp_path: Path, monkeypatch: Any) -> None:
    seen_urls: list[str] = []

    def fake_get(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30):
        seen_urls.append(url)
        if url.startswith("https://r.jina.ai/"):
            raise urllib.error.URLError("reader unavailable")
        return 200, "Moscow: +17C"

    monkeypatch.setattr(code_tools, "_http_get_text", fake_get)
    workspace = _workspace(tmp_path)

    result = web_fetch(workspace, {"url": "https://wttr.in/Moscow?format=3", "retry_attempts": 1})

    assert result["provider"] == "direct"
    assert result["content"] == "Moscow: +17C"
    assert seen_urls == [
        "https://r.jina.ai/https://wttr.in/Moscow?format=3",
        "https://wttr.in/Moscow?format=3",
    ]


def test_web_search_retries_transient_failures(tmp_path: Path, monkeypatch: Any) -> None:
    calls = 0

    def fake_get(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise urllib.error.URLError("temporary failure")
        payload = {
            "results": [
                {
                    "title": "Recovered",
                    "url": "https://example.com/recovered",
                    "content": "Recovered after retries.",
                }
            ]
        }
        return 200, json.dumps(payload)

    monkeypatch.setattr(code_tools, "_http_get_text", fake_get)
    workspace = _workspace(tmp_path)

    result = web_search(
        workspace,
        {
            "query": "retry test",
            "provider": "searxng",
            "retry_attempts": 3,
            "retry_delay_seconds": 0,
        },
    )

    assert calls == 3
    assert result["results"][0]["title"] == "Recovered"


def test_jina_search_retries_without_stale_auth(tmp_path: Path, monkeypatch: Any) -> None:
    seen_headers: list[dict[str, str] | None] = []

    def fake_get(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30):
        seen_headers.append(headers)
        if headers and "Authorization" in headers:
            raise urllib.error.HTTPError(url, 401, "Unauthorized", hdrs=None, fp=None)
        return 200, "Result without auth"

    monkeypatch.setenv("JINA_API_KEY", "stale")
    monkeypatch.setattr(code_tools, "_http_get_text", fake_get)
    workspace = _workspace(tmp_path)

    result = web_search(workspace, {"query": "python venv", "provider": "jina"})

    assert result["provider"] == "jina"
    assert result["results"][0]["content"] == "Result without auth"
    assert seen_headers == [
        {"Accept": "text/plain", "Authorization": "Bearer stale"},
        {"Accept": "text/plain"},
    ]


def test_web_config_reads_gitignored_local_toml(tmp_path: Path, monkeypatch: Any) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "local.toml").write_text(
        '[web]\nbrave_api_key = "from-local-config"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(code_tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(code_tools, "_LOCAL_CONFIG_CACHE", None)

    assert code_tools._brave_api_key() == "from-local-config"


def test_web_search_uses_gigachat_credentials_from_config(tmp_path: Path, monkeypatch: Any) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "local.toml").write_text(
        """
[gigachat]
client_id = "client"
client_secret = "secret"
scope = "GIGACHAT_API_PERS"
auth_url = "https://auth.example/token"
base_url = "https://gigachat.example/api/v1"
model = "GigaChat"
""",
        encoding="utf-8",
    )
    seen: list[tuple[str, dict[str, str] | None, bytes | None]] = []

    def fake_post(
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
        timeout: int = 30,
        verify_ssl_certs: bool = True,
    ):
        seen.append((url, headers, data))
        if url.endswith("/token"):
            return 200, json.dumps({"access_token": "token", "expires_at": 4_102_444_800_000})
        return 200, json.dumps({"choices": [{"message": {"content": "GigaChat answer"}}]})

    monkeypatch.setattr(code_tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(code_tools, "_LOCAL_CONFIG_CACHE", None)
    monkeypatch.setattr(code_tools, "_GIGACHAT_TOKEN_CACHE", {})
    monkeypatch.setattr(code_tools, "_http_post_text", fake_post)
    workspace = _workspace(tmp_path)

    result = web_search(workspace, {"query": "test", "provider": "gigachat", "retry_attempts": 1})

    assert result["provider"] == "gigachat"
    assert result["results"][0]["content"] == "GigaChat answer"
    expected_basic = base64.b64encode(b"client:secret").decode("ascii")
    assert seen[0][1]["Authorization"] == f"Basic {expected_basic}"
    assert seen[0][2] == b"scope=GIGACHAT_API_PERS"
    assert seen[1][1]["Authorization"] == "Bearer token"


def test_web_search_uses_rambler_proxy_from_config(tmp_path: Path, monkeypatch: Any) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "local.toml").write_text(
        """
[rambler_proxy]
url = "https://rambler.example/proxy"
method = "GET"
query_param = "query"
auth = "none"
""",
        encoding="utf-8",
    )
    seen_urls: list[str] = []

    def fake_get(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30):
        seen_urls.append(url)
        return 200, json.dumps(
            {
                "results": [
                    {
                        "title": "Rambler result",
                        "url": "https://example.com/rambler",
                        "snippet": "From Rambler proxy.",
                    }
                ]
            }
        )

    monkeypatch.setattr(code_tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(code_tools, "_LOCAL_CONFIG_CACHE", None)
    monkeypatch.setattr(code_tools, "_http_get_text", fake_get)
    workspace = _workspace(tmp_path)

    result = web_search(workspace, {"query": "python latest", "provider": "rambler_proxy"})

    assert result["provider"] == "rambler_proxy"
    assert result["results"][0]["title"] == "Rambler result"
    assert seen_urls == ["https://rambler.example/proxy?query=python+latest"]


def test_web_search_uses_yandex_search_from_config(tmp_path: Path, monkeypatch: Any) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "local.toml").write_text(
        """
[yandex_search]
api_key = "key"
folder_id = "folder"
search_type = "SEARCH_TYPE_RU"
response_format = "FORMAT_HTML"
""",
        encoding="utf-8",
    )
    seen: list[tuple[str, dict[str, str] | None, bytes | None]] = []
    html = '<html><body><a href="https://python.org/">Python</a></body></html>'

    def fake_post(
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
        timeout: int = 30,
        verify_ssl_certs: bool = True,
    ):
        seen.append((url, headers, data))
        return 200, json.dumps({"rawData": base64.b64encode(html.encode()).decode()})

    monkeypatch.setattr(code_tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(code_tools, "_LOCAL_CONFIG_CACHE", None)
    monkeypatch.setattr(code_tools, "_http_post_text", fake_post)
    workspace = _workspace(tmp_path)

    result = web_search(workspace, {"query": "python latest", "provider": "yandex"})

    assert result["provider"] == "yandex"
    assert result["results"][0]["url"] == "https://python.org/"
    assert seen[0][0] == "https://searchapi.api.cloud.yandex.net/v2/web/search"
    assert seen[0][1]["Authorization"] == "Api-Key key"
    payload = json.loads(seen[0][2].decode())
    assert payload["folderId"] == "folder"
    assert payload["query"]["queryText"] == "python latest"


def test_docs_search_restricts_domain_and_fetches(tmp_path: Path, monkeypatch: Any) -> None:
    seen_urls: list[str] = []

    def fake_get(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30):
        seen_urls.append(url)
        if "/search?" in url:
            payload = {
                "results": [
                    {
                        "title": "tmp_path fixture",
                        "url": "https://docs.pytest.org/en/stable/how-to/tmp_path.html",
                        "content": "tmp_path docs",
                    }
                ]
            }
            return 200, json.dumps(payload)
        return 200, "# tmp_path\nTemporary directories."

    monkeypatch.setattr(code_tools, "_http_get_text", fake_get)
    workspace = _workspace(tmp_path)

    result = docs_search(
        workspace,
        {
            "query": "tmp_path fixture",
            "domain": "docs.pytest.org",
            "provider": "searxng",
            "fetch_results": True,
        },
    )

    assert result["docs_query"] == "site:docs.pytest.org tmp_path fixture"
    assert result["search"]["results"][0]["title"] == "tmp_path fixture"
    assert result["fetched"][0]["content"].startswith("# tmp_path")
    assert any("site%3Adocs.pytest.org" in url for url in seen_urls)


def test_agent_exposes_web_tools_with_network_permission(tmp_path: Path) -> None:
    agent = AgentCore.from_workspace(tmp_path)
    names = {tool.name for tool in agent.tools.list()}

    assert {"web_search", "docs_search", "curl"}.issubset(names)
    assert "web_fetch" not in names
    assert agent.tools.get("curl").permission.value == "network"
    assert "Example:" in agent.tools.get("web_search").description

    result = agent.call_tool("web_search", {"query": "python docs"})
    assert result.confirmation_required is True
    assert result.result["status"] == "confirmation_required"
