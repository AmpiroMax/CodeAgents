"""Web fetching, search, and HTML→Markdown helpers.

All providers (Jina, SearxNG, Brave, GigaChat, Yandex, Ollama Cloud,
Rambler proxy), the SQLite-backed cache, and HTTP utilities live here.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import sqlite3
import ssl
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from codeagents.core.workspace import Workspace, WorkspaceError


PROJECT_ROOT = Path(__file__).resolve().parents[3]
_LOCAL_CONFIG_CACHE: dict[str, Any] | None = None
_GIGACHAT_TOKEN_CACHE: dict[str, Any] = {}

# Mutable index that flips between [ollama, yandex] across consecutive ``auto``
# calls so neither cloud provider gets all the traffic.
_AUTO_SEARCH_RR_INDEX = {"i": 0}


def _require_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing required string argument: {key}")
    return value


def _require_url(args: dict[str, Any], key: str) -> str:
    url = _require_str(args, key).strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{key} must be an http(s) URL")
    return url


def _looks_like_pdf_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    path = (parsed.path or "").lower()
    if path.endswith(".pdf"):
        return True
    host = (parsed.netloc or "").lower()
    if host.endswith("arxiv.org") and path.startswith("/pdf/"):
        return True
    return False


# ── HTTP primitives ───────────────────────────────────────────────────


def _http_get_text(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30) -> tuple[int, str]:
    status, response_headers, data = _http_request_bytes(url, headers=headers, timeout=timeout)
    return status, _decode_http_body(data, headers=response_headers)


def _http_request_bytes(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 30,
    verify_ssl_certs: bool = True,
) -> tuple[int, Any, bytes]:
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "CodeAgents/0.1 (+local-agent)",
            "Accept-Encoding": "gzip, deflate",
            **(headers or {}),
        },
        method=method,
    )
    context = None if verify_ssl_certs else ssl._create_unverified_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        data = response.read()
        return response.status, response.headers, data


def _http_post_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 30,
    verify_ssl_certs: bool = True,
) -> tuple[int, str]:
    status, response_headers, payload = _http_request_bytes(
        url,
        method="POST",
        data=data,
        headers=headers,
        timeout=timeout,
        verify_ssl_certs=verify_ssl_certs,
    )
    return status, _decode_http_body(payload, headers=response_headers)


def _decode_http_body(data: bytes, *, headers: Any) -> str:
    data = _decode_http_bytes(data, headers=headers)
    charset = headers.get_content_charset() if hasattr(headers, "get_content_charset") else None
    return data.decode(charset or "utf-8", errors="replace")


def _decode_http_bytes(data: bytes, *, headers: Any) -> bytes:
    encoding = str(headers.get("Content-Encoding", "")).lower()
    if "gzip" in encoding:
        return gzip.decompress(data)
    elif "deflate" in encoding:
        try:
            return zlib.decompress(data)
        except zlib.error:
            return zlib.decompress(data, -zlib.MAX_WBITS)
    return data


def _http_error_text(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    return f"HTTP {exc.code} {exc.reason}: {body[:1000]}"


# ── curl helpers ──────────────────────────────────────────────────────


def _curl_body(args: dict[str, Any]) -> bytes | None:
    has_data = args.get("data") is not None
    has_json = args.get("json") is not None
    if has_data and has_json:
        raise ValueError("Pass either data or json, not both")
    if has_json:
        return json.dumps(args["json"], ensure_ascii=False).encode("utf-8")
    if has_data:
        value = args["data"]
        if isinstance(value, str):
            return value.encode("utf-8")
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False).encode("utf-8")
        raise ValueError("data must be a string, object, or array")
    return None


def _string_dict(value: Any, name: str) -> dict[str, str]:
    if value is None or value == "":
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return {str(k): str(v) for k, v in value.items()}


def _curl_output_path(workspace: Workspace, output_path: str) -> Path:
    try:
        path = workspace.resolve_inside(output_path)
    except WorkspaceError as exc:
        raise ValueError(str(exc)) from exc
    try:
        rel_path = path.relative_to(workspace.root)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {output_path}") from exc
    if path == workspace.root:
        raise ValueError("output_path must be a file path, not the workspace root")
    if rel_path.parts and rel_path.parts[0] == ".codeagents":
        raise ValueError("Refusing to write curl output into CodeAgents internal state")
    return path


def _public_response_headers(headers: Any) -> dict[str, str]:
    keep = {
        "content-type",
        "content-length",
        "content-encoding",
        "etag",
        "last-modified",
        "location",
    }
    result: dict[str, str] = {}
    for key in headers.keys():
        if str(key).lower() in keep:
            result[str(key)] = str(headers.get(key, ""))
    return result


# ── Local config / retries ────────────────────────────────────────────


def _local_config() -> dict[str, Any]:
    global _LOCAL_CONFIG_CACHE
    if _LOCAL_CONFIG_CACHE is not None:
        return _LOCAL_CONFIG_CACHE

    merged: dict[str, Any] = {}
    for path in (
        PROJECT_ROOT / "config" / "local.toml",
        PROJECT_ROOT / ".codeagents" / "secrets.toml",
    ):
        if not path.exists():
            continue
        try:
            with path.open("rb") as handle:
                raw = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        _deep_merge(merged, raw)
    _LOCAL_CONFIG_CACHE = merged
    return merged


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


def _config_value(section: str, key: str, *env_names: str) -> str | None:
    value = _local_config().get(section, {}).get(key)
    if value is not None and value != "":
        return str(value)
    for env_name in env_names:
        env_value = os.getenv(env_name)
        if env_value:
            return env_value
    return None


def _retry_attempts(args: dict[str, Any]) -> int:
    return max(1, min(int(args.get("retry_attempts", 5)), 15))


def _retry_delay_seconds(args: dict[str, Any]) -> float:
    return max(0.0, min(float(args.get("retry_delay_seconds", 0.25)), 5.0))


def _call_with_retries(func: Any, *, attempts: int, delay_seconds: float) -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:
            if not _is_retryable_web_error(exc) or attempt >= attempts:
                raise
            last_exc = exc
            if delay_seconds > 0:
                time.sleep(delay_seconds)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("retry loop exited without result")


def _is_retryable_web_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {408, 409, 425, 429} or exc.code >= 500
    if isinstance(exc, urllib.error.URLError):
        return True
    return isinstance(exc, (TimeoutError, OSError))


def _round_robin_pick(candidates: list[str]) -> list[str]:
    if not candidates:
        return candidates
    state = _AUTO_SEARCH_RR_INDEX
    head = state["i"] % len(candidates)
    state["i"] = (head + 1) % max(1, len(candidates))
    return candidates[head:] + candidates[:head]


# ── Jina ──────────────────────────────────────────────────────────────


def _jina_headers() -> dict[str, str]:
    api_key = _config_value("web", "jina_api_key", "JINA_API_KEY", "CODEAGENTS_JINA_API_KEY")
    headers = {"Accept": "text/plain"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _jina_reader_fetch(url: str, *, timeout: int) -> tuple[int, str]:
    reader_url = f"https://r.jina.ai/{url}"
    return _jina_get_text_with_auth_retry(reader_url, timeout=timeout)


def _jina_get_text_with_auth_retry(url: str, *, timeout: int) -> tuple[int, str]:
    headers = _jina_headers()
    try:
        return _http_get_text(url, headers=headers, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code != 401 or "Authorization" not in headers:
            raise
        # A stale/invalid local Jina key should not break the free no-key path.
        return _http_get_text(url, headers={"Accept": "text/plain"}, timeout=timeout)


def _jina_search(*, query: str, limit: int, timeout: int) -> dict[str, Any]:
    encoded = urllib.parse.quote(query)
    url = f"https://s.jina.ai/{encoded}"
    status, text = _jina_get_text_with_auth_retry(url, timeout=timeout)
    return {
        "query": query,
        "provider": "jina",
        "status": status,
        "results": [{
            "title": f"Jina Search: {query}",
            "url": url,
            "snippet": text[:2000],
            "content": text,
        }][:limit],
    }


# ── SearxNG / Brave ───────────────────────────────────────────────────


def _searxng_search(args: dict[str, Any], *, query: str, limit: int) -> dict[str, Any]:
    base_url = str(
        args.get("searxng_url")
        or _config_value("web", "searxng_url", "CODEAGENTS_SEARXNG_URL")
        or "http://127.0.0.1:8080"
    ).rstrip("/")
    params: dict[str, Any] = {
        "q": query,
        "format": "json",
        "language": args.get("language", "en"),
    }
    if args.get("time_range"):
        params["time_range"] = args["time_range"]
    if args.get("categories"):
        categories = args["categories"]
        params["categories"] = ",".join(categories) if isinstance(categories, list) else str(categories)
    url = f"{base_url}/search?{urllib.parse.urlencode(params)}"
    status, text = _http_get_text(url, timeout=int(args.get("timeout", 10)))
    raw = json.loads(text)
    results = []
    for item in raw.get("results", [])[:limit]:
        if not isinstance(item, dict):
            continue
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
            "engine": item.get("engine", ""),
        })
    return {
        "query": query,
        "provider": "searxng",
        "status": status,
        "searxng_url": base_url,
        "results": results,
    }


def _brave_search(args: dict[str, Any], *, query: str, limit: int) -> dict[str, Any]:
    api_key = _brave_api_key()
    if not api_key:
        raise ValueError("BRAVE_API_KEY or CODEAGENTS_BRAVE_API_KEY is not set")
    params = {
        "q": query,
        "count": str(limit),
    }
    if args.get("language"):
        params["search_lang"] = str(args["language"])
    if args.get("country"):
        params["country"] = str(args["country"])
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(params)
    status, text = _http_get_text(
        url,
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        },
        timeout=int(args.get("timeout", 30)),
    )
    raw = json.loads(text)
    results = []
    for item in raw.get("web", {}).get("results", [])[:limit]:
        if not isinstance(item, dict):
            continue
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("description", ""),
            "engine": "brave",
        })
    return {"query": query, "provider": "brave", "status": status, "results": results}


def _brave_api_key() -> str | None:
    return _config_value("web", "brave_api_key", "BRAVE_API_KEY", "CODEAGENTS_BRAVE_API_KEY")


# ── GigaChat ──────────────────────────────────────────────────────────


def _gigachat_configured() -> bool:
    return bool(
        _config_value("gigachat", "authorization_key", "GIGACHAT_CREDENTIALS")
        or (
            _config_value("gigachat", "client_id", "GIGACHAT_CLIENT_ID")
            and _config_value("gigachat", "client_secret", "GIGACHAT_CLIENT_SECRET")
        )
        or _config_value("gigachat", "access_token", "GIGACHAT_ACCESS_TOKEN")
    )


def _gigachat_search(args: dict[str, Any], *, query: str, limit: int) -> dict[str, Any]:
    timeout = int(args.get("timeout", 30))
    token = _gigachat_access_token(timeout=timeout)
    base_url = (
        _config_value("gigachat", "base_url", "GIGACHAT_BASE_URL")
        or "https://gigachat.devices.sberbank.ru/api/v1"
    ).rstrip("/")
    model = _config_value("gigachat", "model", "GIGACHAT_MODEL") or "GigaChat"
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": int(args.get("max_tokens", 1200)),
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты поисковый помощник для кодового агента. "
                    "Дай краткий ответ на запрос и, если знаешь, перечисли релевантные URL. "
                    "Не выдумывай ссылки, если не уверен."
                ),
            },
            {"role": "user", "content": query},
        ],
    }
    try:
        status, text = _http_post_text(
            f"{base_url}/chat/completions",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
            verify_ssl_certs=_gigachat_verify_ssl_certs(),
        )
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GigaChat chat failed: {_http_error_text(exc)}") from exc
    raw = json.loads(text)
    content = ""
    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = str(message.get("content", ""))
    return {
        "query": query,
        "provider": "gigachat",
        "status": status,
        "results": [
            {
                "title": f"GigaChat answer: {query}",
                "url": "",
                "snippet": content[:2000],
                "content": content,
                "engine": "gigachat",
            }
        ][:limit],
    }


def _gigachat_access_token(*, timeout: int) -> str:
    configured_token = _config_value("gigachat", "access_token", "GIGACHAT_ACCESS_TOKEN")
    if configured_token:
        return configured_token

    now = time.time()
    cached_token = _GIGACHAT_TOKEN_CACHE.get("access_token")
    expires_at = float(_GIGACHAT_TOKEN_CACHE.get("expires_at", 0))
    if cached_token and expires_at - 60 > now:
        return str(cached_token)

    authorization_key = _gigachat_authorization_key()
    if not authorization_key:
        raise ValueError(
            "GigaChat credentials are not configured. Set gigachat.authorization_key "
            "or gigachat.client_id/client_secret in config/local.toml or .codeagents/secrets.toml."
        )
    auth_url = (
        _config_value("gigachat", "auth_url", "GIGACHAT_AUTH_URL")
        or "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    )
    scope = _config_value("gigachat", "scope", "GIGACHAT_SCOPE") or "GIGACHAT_API_PERS"
    try:
        status, text = _http_post_text(
            auth_url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "RqUID": str(uuid.uuid4()),
                "Authorization": f"Basic {authorization_key}",
            },
            data=urllib.parse.urlencode({"scope": scope}).encode("utf-8"),
            timeout=timeout,
            verify_ssl_certs=_gigachat_verify_ssl_certs(),
        )
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GigaChat OAuth failed: {_http_error_text(exc)}") from exc
    raw = json.loads(text)
    token = raw.get("access_token")
    if not token:
        raise ValueError(f"GigaChat OAuth response did not contain access_token (status={status})")
    expires_at_raw = raw.get("expires_at")
    if isinstance(expires_at_raw, (int, float)):
        expires_at = float(expires_at_raw)
        if expires_at > 10_000_000_000:
            expires_at = expires_at / 1000
    else:
        expires_at = now + 30 * 60
    _GIGACHAT_TOKEN_CACHE["access_token"] = token
    _GIGACHAT_TOKEN_CACHE["expires_at"] = expires_at
    return str(token)


def _gigachat_authorization_key() -> str | None:
    authorization_key = (
        _config_value("gigachat", "authorization_key", "GIGACHAT_CREDENTIALS")
        or _config_value("gigachat", "auth_key", "GIGACHAT_AUTH_KEY")
    )
    if authorization_key:
        return authorization_key
    client_id = _config_value("gigachat", "client_id", "GIGACHAT_CLIENT_ID")
    client_secret = _config_value("gigachat", "client_secret", "GIGACHAT_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    return base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")


def _gigachat_verify_ssl_certs() -> bool:
    value = _config_value("gigachat", "verify_ssl_certs", "GIGACHAT_VERIFY_SSL_CERTS")
    if value is None:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}


# ── Rambler proxy ─────────────────────────────────────────────────────


def _rambler_proxy_configured() -> bool:
    return bool(_rambler_proxy_url({}))


def _rambler_proxy_search(args: dict[str, Any], *, query: str, limit: int) -> dict[str, Any]:
    endpoint = _rambler_proxy_url(args)
    if not endpoint:
        raise ValueError(
            "Rambler proxy URL is not configured. Set rambler_proxy.url in config/local.toml "
            "or RAMBLER_PROXY_URL/CODEAGENTS_RAMBLER_PROXY_URL."
        )

    timeout = int(args.get("timeout", 30))
    method = str(
        args.get("rambler_proxy_method")
        or _config_value("rambler_proxy", "method", "RAMBLER_PROXY_METHOD")
        or "GET"
    ).upper()
    query_param = str(
        args.get("rambler_proxy_query_param")
        or _config_value("rambler_proxy", "query_param", "RAMBLER_PROXY_QUERY_PARAM")
        or "query"
    )
    headers = _rambler_proxy_headers(timeout=timeout)
    if method == "GET":
        params = {query_param: query}
        limit_param = _config_value("rambler_proxy", "limit_param", "RAMBLER_PROXY_LIMIT_PARAM")
        if limit_param:
            params[limit_param] = str(limit)
        separator = "&" if urllib.parse.urlparse(endpoint).query else "?"
        url = endpoint + separator + urllib.parse.urlencode(params)
        status, text = _http_get_text(url, headers=headers, timeout=timeout)
    elif method == "POST":
        body_field = str(
            args.get("rambler_proxy_body_field")
            or _config_value("rambler_proxy", "body_field", "RAMBLER_PROXY_BODY_FIELD")
            or "query"
        )
        payload = {body_field: query, "limit": limit}
        status, text = _http_post_text(
            endpoint,
            headers={**headers, "Content-Type": "application/json"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
            verify_ssl_certs=_rambler_proxy_verify_ssl_certs(),
        )
    else:
        raise ValueError("rambler_proxy method must be GET or POST")

    return _rambler_proxy_result(query=query, status=status, text=text, limit=limit)


def _rambler_proxy_url(args: dict[str, Any]) -> str | None:
    value = (
        args.get("rambler_proxy_url")
        or _config_value("rambler_proxy", "url", "RAMBLER_PROXY_URL", "CODEAGENTS_RAMBLER_PROXY_URL")
    )
    return str(value).rstrip("/") if value else None


def _rambler_proxy_headers(*, timeout: int) -> dict[str, str]:
    headers = {"Accept": "application/json, text/plain, */*"}
    auth = str(_config_value("rambler_proxy", "auth", "RAMBLER_PROXY_AUTH") or "gigachat_bearer").lower()
    if auth in {"none", "no", "off", "false"}:
        return headers
    if auth == "gigachat_bearer":
        headers["Authorization"] = f"Bearer {_gigachat_access_token(timeout=timeout)}"
        return headers
    if auth == "bearer":
        token = _config_value("rambler_proxy", "bearer_token", "RAMBLER_PROXY_BEARER_TOKEN")
        if not token:
            raise ValueError("rambler_proxy.auth='bearer' requires rambler_proxy.bearer_token")
        headers["Authorization"] = f"Bearer {token}"
        return headers
    raise ValueError("rambler_proxy.auth must be gigachat_bearer, bearer, or none")


def _rambler_proxy_verify_ssl_certs() -> bool:
    value = _config_value("rambler_proxy", "verify_ssl_certs", "RAMBLER_PROXY_VERIFY_SSL_CERTS")
    if value is None:
        return _gigachat_verify_ssl_certs()
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _rambler_proxy_result(*, query: str, status: int, text: str, limit: int) -> dict[str, Any]:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return {
            "query": query,
            "provider": "rambler_proxy",
            "status": status,
            "results": [{
                "title": f"Rambler proxy: {query}",
                "url": "",
                "snippet": text[:2000],
                "content": text,
                "engine": "rambler_proxy",
            }][:limit],
        }
    results = _extract_search_results(raw, limit=limit, engine="rambler_proxy")
    if not results:
        results = [{
            "title": f"Rambler proxy: {query}",
            "url": "",
            "snippet": json.dumps(raw, ensure_ascii=False)[:2000],
            "content": json.dumps(raw, ensure_ascii=False),
            "engine": "rambler_proxy",
        }]
    return {"query": query, "provider": "rambler_proxy", "status": status, "results": results[:limit]}


# ── Yandex Search ─────────────────────────────────────────────────────


def _yandex_search_configured() -> bool:
    api_key = _config_value("yandex_search", "api_key", "YANDEX_SEARCH_API_KEY", "YANDEX_API_KEY")
    folder_id = _config_value("yandex_search", "folder_id", "YANDEX_SEARCH_FOLDER_ID", "YANDEX_FOLDER_ID")
    return bool(api_key and folder_id and folder_id != "...")


def _yandex_search(args: dict[str, Any], *, query: str, limit: int) -> dict[str, Any]:
    api_key = _config_value("yandex_search", "api_key", "YANDEX_SEARCH_API_KEY", "YANDEX_API_KEY")
    folder_id = _config_value("yandex_search", "folder_id", "YANDEX_SEARCH_FOLDER_ID", "YANDEX_FOLDER_ID")
    if not api_key:
        raise ValueError("Yandex Search API key is not configured")
    if not folder_id or folder_id == "...":
        raise ValueError("Yandex Search folder_id is not configured")

    endpoint = (
        args.get("yandex_search_url")
        or _config_value("yandex_search", "url", "YANDEX_SEARCH_URL")
        or "https://searchapi.api.cloud.yandex.net/v2/web/search"
    )
    search_type = str(
        args.get("yandex_search_type")
        or _config_value("yandex_search", "search_type", "YANDEX_SEARCH_TYPE")
        or "SEARCH_TYPE_RU"
    )
    response_format = str(
        args.get("yandex_response_format")
        or _config_value("yandex_search", "response_format", "YANDEX_SEARCH_RESPONSE_FORMAT")
        or "FORMAT_HTML"
    )
    payload = {
        "query": {
            "searchType": search_type,
            "queryText": query,
        },
        "folderId": folder_id,
        "responseFormat": response_format,
    }
    status, text = _http_post_text(
        str(endpoint),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Api-Key {api_key}",
        },
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=int(args.get("timeout", 30)),
    )
    raw = json.loads(text)
    html_content = _decode_yandex_raw_data(raw)
    cleaned = _clean_html_content(html_content)
    content = cleaned["text"] if cleaned["is_html"] else html_content
    results = _extract_search_results(raw, limit=limit, engine="yandex")
    if html_content:
        html_results = _extract_html_search_results(html_content, limit=limit)
        if html_results:
            results = html_results
    if not results:
        results = [{
            "title": f"Yandex Search: {query}",
            "url": "",
            "snippet": content[:2000] if content else json.dumps(raw, ensure_ascii=False)[:2000],
            "content": content or json.dumps(raw, ensure_ascii=False),
            "engine": "yandex",
        }]
    return {
        "query": query,
        "provider": "yandex",
        "status": status,
        "results": results[:limit],
        "content": content[:12000],
        "cleaned": cleaned["is_html"],
    }


def _decode_yandex_raw_data(raw: dict[str, Any]) -> str:
    raw_data = raw.get("rawData")
    if not isinstance(raw_data, str) or not raw_data:
        return ""
    try:
        return base64.b64decode(raw_data).decode("utf-8", errors="replace")
    except Exception:
        return raw_data


# ── Ollama Cloud search/fetch ─────────────────────────────────────────


def _ollama_search_api_key() -> str | None:
    """Return Ollama Cloud API key for the web_search/web_fetch endpoints."""

    return (
        _config_value("ollama_search", "api_key", "OLLAMA_API_KEY", "CODEAGENTS_OLLAMA_API_KEY")
        or _config_value("ollama", "api_key", "OLLAMA_API_KEY", "CODEAGENTS_OLLAMA_API_KEY")
    )


def _ollama_search_configured() -> bool:
    return bool(_ollama_search_api_key())


def _ollama_search(args: dict[str, Any], *, query: str, limit: int) -> dict[str, Any]:
    api_key = _ollama_search_api_key()
    if not api_key:
        raise ValueError("Ollama web_search API key is not configured")
    endpoint = (
        args.get("ollama_search_url")
        or _config_value("ollama_search", "url", "OLLAMA_SEARCH_URL")
        or "https://ollama.com/api/web_search"
    )
    payload = {"query": query, "max_results": max(1, min(limit, 10))}
    status, text = _http_post_text(
        str(endpoint),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=int(args.get("timeout", 30)),
    )
    raw = json.loads(text)
    raw_results = raw.get("results") or []
    results: list[dict[str, Any]] = []
    for item in raw_results[:limit]:
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "title": str(item.get("title") or "")[:300],
                "url": str(item.get("url") or ""),
                "snippet": str(item.get("content") or "")[:1500],
                "engine": "ollama",
            }
        )
    return {
        "query": query,
        "provider": "ollama",
        "status": status,
        "results": results,
        "content": "\n\n".join(r.get("snippet", "") for r in results)[:12000],
        "cleaned": False,
    }


def _ollama_fetch_raw(url: str, *, timeout: int) -> tuple[int, str]:
    api_key = _ollama_search_api_key()
    if not api_key:
        raise ValueError("Ollama web_fetch API key is not configured")
    endpoint = (
        _config_value("ollama_search", "fetch_url", "OLLAMA_FETCH_URL")
        or "https://ollama.com/api/web_fetch"
    )
    status, text = _http_post_text(
        endpoint,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        data=json.dumps({"url": url}, ensure_ascii=False).encode("utf-8"),
        timeout=timeout,
    )
    raw = json.loads(text)
    return status, str(raw.get("content") or "")


# ── HTML cleaning / extraction ────────────────────────────────────────


def _to_markdown(text: str) -> str:
    """Convert HTML to Markdown with ``markdownify``; passes plain text through."""

    if not text or not _looks_like_html(text):
        return text or ""
    try:
        from markdownify import markdownify as _md  # type: ignore
    except ImportError:
        return text
    try:
        return _md(text, heading_style="ATX", strip=["script", "style"])
    except Exception:
        return text


def _extract_html_search_results(html: str, *, limit: int) -> list[dict[str, Any]]:
    if not _looks_like_html(html):
        return []
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        if not href.startswith(("http://", "https://")):
            continue
        if href in seen or _is_low_value_search_url(href):
            continue
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title:
            continue
        seen.add(href)
        results.append({"title": title, "url": href, "snippet": title, "engine": "yandex"})
        if len(results) >= limit:
            break
    return results


def _clean_html_content(content: str) -> dict[str, Any]:
    if not _looks_like_html(content):
        return {"is_html": False, "text": content, "links": []}

    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(
        [
            "script",
            "style",
            "noscript",
            "template",
            "svg",
            "canvas",
            "form",
            "input",
            "button",
            "header",
            "footer",
            "nav",
            "aside",
        ]
    ):
        tag.decompose()

    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if not href.startswith(("http://", "https://")):
            continue
        if href in seen or _is_low_value_search_url(href):
            continue
        text = _normalize_text(anchor.get_text(" ", strip=True))
        links.append({"text": text, "url": href})
        seen.add(href)
        if len(links) >= 50:
            break

    title = _normalize_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
    text = _normalize_text(soup.get_text("\n", strip=True))
    if title and not text.startswith(title):
        text = f"{title}\n\n{text}" if text else title
    if links:
        link_lines = [
            f"- {item['text'] or item['url']}: {item['url']}"
            for item in links[:25]
        ]
        text = f"{text}\n\nLinks:\n" + "\n".join(link_lines)

    return {"is_html": True, "text": text, "links": links}


def _looks_like_html(content: str) -> bool:
    sample = content[:1000].lower()
    return any(marker in sample for marker in ("<html", "<body", "<!doctype html", "<head", "<script", "<div", "<p", "<a "))


def _normalize_text(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.replace("\r", "\n").split("\n")]
    compact: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if not previous_blank:
                compact.append("")
            previous_blank = True
            continue
        compact.append(line)
        previous_blank = False
    return "\n".join(compact).strip()


def _extract_search_results(raw: Any, *, limit: int, engine: str) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        candidates = raw
    elif isinstance(raw, dict):
        candidates = []
        for key in ("results", "items", "documents", "data", "organic", "web"):
            value = raw.get(key)
            if isinstance(value, list):
                candidates = value
                break
            if isinstance(value, dict):
                nested = _extract_search_results(value, limit=limit, engine=engine)
                if nested:
                    return nested
        if not candidates and all(isinstance(raw.get(key), str) for key in ("title", "url")):
            candidates = [raw]
    else:
        return []

    results: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or item.get("heading") or "")
        url = str(item.get("url") or item.get("link") or item.get("href") or "")
        snippet = str(item.get("snippet") or item.get("content") or item.get("description") or item.get("text") or "")
        if not title and not url and not snippet:
            continue
        results.append({"title": title, "url": url, "snippet": snippet, "engine": engine})
        if len(results) >= limit:
            break
    return results


def _is_low_value_search_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    return (
        host == "passport.yandex.ru"
        or host.endswith(".passport.yandex.ru")
        or url.startswith("https://yandex.ru/alice/")
        or url.startswith("https://ya.ru/alice/")
    )


# ── SQLite-backed cache ───────────────────────────────────────────────


def _web_cache_path(workspace: Workspace) -> Path:
    return workspace.root / ".codeagents" / "web_cache.sqlite3"


def _web_cache_get(workspace: Workspace, key: str, *, ttl_seconds: int) -> dict[str, Any] | None:
    path = _web_cache_path(workspace)
    if not path.exists():
        return None
    with sqlite3.connect(path) as conn:
        _init_web_cache(conn)
        row = conn.execute("select payload, created_at from web_cache where key = ?", (key,)).fetchone()
    if row is None:
        return None
    if ttl_seconds >= 0 and time.time() - float(row[1]) > ttl_seconds:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def _web_cache_put(workspace: Workspace, key: str, payload: dict[str, Any]) -> None:
    path = _web_cache_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        _init_web_cache(conn)
        conn.execute(
            """
            insert into web_cache(key, payload, created_at)
            values (?, ?, ?)
            on conflict(key) do update set
              payload = excluded.payload,
              created_at = excluded.created_at
            """,
            (key, json.dumps(payload, ensure_ascii=False), time.time()),
        )


def _init_web_cache(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists web_cache (
          key text primary key,
          payload text not null,
          created_at real not null
        )
        """
    )


# ── Tool handlers ─────────────────────────────────────────────────────


def web_fetch(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    url = _require_url(args, "url")
    max_chars = int(args.get("max_chars", 12000))
    timeout = int(args.get("timeout", 30))
    retry_attempts = _retry_attempts(args)
    retry_delay_seconds = _retry_delay_seconds(args)
    no_cache = bool(args.get("no_cache", False))
    provider = str(args.get("provider", "auto")).lower()
    if provider not in {"auto", "jina", "direct", "ollama"}:
        raise ValueError("web_fetch provider must be auto, jina, direct, or ollama")

    # If the URL clearly points at a PDF (extension or arxiv.org/pdf path),
    # short-circuit to ``read_pdf`` so the model gets actual text instead
    # of a garbled binary blob from the HTML pipeline.
    if _looks_like_pdf_url(url):
        try:
            from codeagents.tools.pdf import read_pdf

            pdf_res = read_pdf(
                workspace,
                {"url": url, "max_chars": max_chars, "timeout": max(timeout, 45)},
            )
        except Exception as exc:
            return {"error": f"web_fetch->read_pdf failed: {exc}", "url": url}
        if "error" in pdf_res:
            if "bad signature" not in str(pdf_res.get("error", "")):
                return {**pdf_res, "url": url, "provider": "pdf"}
        else:
            return {
                "url": url,
                "provider": "pdf",
                "status": 200,
                "content": pdf_res.get("content", "")[:max_chars],
                "content_chars": len(pdf_res.get("content", "")),
                "markdown": pdf_res.get("content", "")[:max_chars],
                "raw_html": "",
                "cleaned": False,
                "links": [],
                "cached": False,
                "errors": [],
                "pdf": {
                    "total_pages": pdf_res.get("total_pages"),
                    "returned_pages": pdf_res.get("returned_pages"),
                    "truncated": pdf_res.get("truncated"),
                    "info": pdf_res.get("info", {}),
                },
            }

    cache_key = f"fetch_v2:{provider}:{url}"
    ttl_seconds = int(args.get("ttl_seconds", 86_400))
    if not no_cache:
        cached = _web_cache_get(workspace, cache_key, ttl_seconds=ttl_seconds)
        if cached is not None:
            cached["cached"] = True
            cached["content"] = str(cached.get("content", ""))[:max_chars]
            cached["markdown"] = str(cached.get("markdown", ""))[:max_chars]
            return cached

    errors: list[str] = []
    status = 0
    text = ""
    used_provider = provider
    if provider == "ollama" or (
        provider == "auto"
        and _ollama_search_configured()
        and bool(args.get("prefer_ollama", False))
    ):
        try:
            status, text = _ollama_fetch_raw(url, timeout=timeout)
            used_provider = "ollama"
        except Exception as exc:
            errors.append(f"ollama: {exc}")
            if provider == "ollama":
                return {"error": f"web_fetch failed: {exc}", "url": url, "provider": provider}
    if not text and provider in {"auto", "jina"}:
        try:
            status, text = _call_with_retries(
                lambda: _jina_reader_fetch(url, timeout=timeout),
                attempts=retry_attempts,
                delay_seconds=retry_delay_seconds,
            )
            used_provider = "jina"
        except Exception as exc:
            errors.append(f"jina: {exc}")
            if provider == "jina":
                return {"error": f"web_fetch failed: {exc}", "url": url, "provider": provider}
    if provider in {"auto", "direct"} and not text:
        try:
            status, text = _call_with_retries(
                lambda: _http_get_text(url, timeout=timeout),
                attempts=retry_attempts,
                delay_seconds=retry_delay_seconds,
            )
            used_provider = "direct"
        except Exception as exc:
            errors.append(f"direct: {exc}")
            return {"error": "web_fetch failed", "url": url, "provider": provider, "errors": errors}
    cleaned = _clean_html_content(text)
    is_html = bool(cleaned["is_html"])
    content = cleaned["text"] if is_html else text
    raw_html = text if is_html else ""
    markdown = _to_markdown(text) if is_html else text
    result = {
        "url": url,
        "provider": used_provider,
        "status": status,
        "content": content[:max_chars],
        "content_chars": len(text),
        "markdown": markdown[:max_chars],
        "raw_html": raw_html[: max(max_chars, 32000)],
        "cleaned": is_html,
        "links": cleaned["links"],
        "cached": False,
        "errors": errors,
    }
    _web_cache_put(workspace, cache_key, result)
    return result


def curl(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    url = _require_url(args, "url")
    method = str(args.get("method", "GET")).upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
        raise ValueError("method must be GET, POST, PUT, PATCH, DELETE, or HEAD")

    timeout = int(args.get("timeout", 30))
    max_chars = int(args.get("max_chars", 12000))
    headers = _string_dict(args.get("headers", {}), "headers")
    data = _curl_body(args)
    if args.get("json") is not None and not any(k.lower() == "content-type" for k in headers):
        headers["Content-Type"] = "application/json"
    output_path = args.get("output_path")
    output_file = _curl_output_path(workspace, str(output_path)) if output_path else None

    status, response_headers, body = _http_request_bytes(
        url,
        method=method,
        headers=headers,
        data=data,
        timeout=timeout,
    )
    content_type = response_headers.get("Content-Type", "")
    if output_file is not None:
        body = _decode_http_bytes(body, headers=response_headers)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(body)
        return {
            "url": url,
            "method": method,
            "status": status,
            "content_type": content_type,
            "bytes": len(body),
            "output_path": str(output_file.relative_to(workspace.root)),
            "headers": _public_response_headers(response_headers),
        }

    text = _decode_http_body(body, headers=response_headers)
    cleaned = _clean_html_content(text)
    content = cleaned["text"] if cleaned["is_html"] else text
    return {
        "url": url,
        "method": method,
        "status": status,
        "content_type": content_type,
        "bytes": len(body),
        "content": content[:max_chars],
        "content_chars": len(content),
        "cleaned": cleaned["is_html"],
        "links": cleaned["links"],
        "headers": _public_response_headers(response_headers),
    }


def web_search(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    query = _require_str(args, "query")
    limit = max(1, min(int(args.get("limit", 5)), 20))
    provider = str(args.get("provider", "auto")).lower()
    retry_attempts = _retry_attempts(args)
    retry_delay_seconds = _retry_delay_seconds(args)
    no_cache = bool(args.get("no_cache", False))
    ttl_seconds = int(args.get("ttl_seconds", 3600))
    cache_key = "search:" + json.dumps(
        {
            "provider": provider,
            "query": query,
            "limit": limit,
            "language": args.get("language"),
            "time_range": args.get("time_range"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    if not no_cache:
        cached = _web_cache_get(workspace, cache_key, ttl_seconds=ttl_seconds)
        if cached is not None:
            cached["cached"] = True
            return cached

    errors: list[str] = []
    if provider == "auto":
        cloud: list[str] = []
        if _ollama_search_configured():
            cloud.append("ollama")
        if _yandex_search_configured():
            cloud.append("yandex")
        cloud = _round_robin_pick(cloud)
        providers = list(cloud)
        providers.extend(["searxng", "jina"])
        if _brave_api_key():
            providers.append("brave")
        if _rambler_proxy_configured():
            providers.append("rambler_proxy")
        if _gigachat_configured():
            providers.append("gigachat")
    else:
        providers = [provider]

    for candidate in providers:
        try:
            if candidate == "searxng":
                result = _call_with_retries(
                    lambda: _searxng_search(args, query=query, limit=limit),
                    attempts=retry_attempts,
                    delay_seconds=retry_delay_seconds,
                )
            elif candidate == "jina":
                result = _call_with_retries(
                    lambda: _jina_search(query=query, limit=limit, timeout=int(args.get("timeout", 30))),
                    attempts=retry_attempts,
                    delay_seconds=retry_delay_seconds,
                )
            elif candidate == "brave":
                result = _call_with_retries(
                    lambda: _brave_search(args, query=query, limit=limit),
                    attempts=retry_attempts,
                    delay_seconds=retry_delay_seconds,
                )
            elif candidate == "gigachat":
                result = _call_with_retries(
                    lambda: _gigachat_search(args, query=query, limit=limit),
                    attempts=retry_attempts,
                    delay_seconds=retry_delay_seconds,
                )
            elif candidate in {"rambler", "rambler_proxy"}:
                result = _call_with_retries(
                    lambda: _rambler_proxy_search(args, query=query, limit=limit),
                    attempts=retry_attempts,
                    delay_seconds=retry_delay_seconds,
                )
            elif candidate in {"yandex", "yandex_search"}:
                result = _call_with_retries(
                    lambda: _yandex_search(args, query=query, limit=limit),
                    attempts=retry_attempts,
                    delay_seconds=retry_delay_seconds,
                )
            elif candidate in {"ollama", "ollama_search"}:
                result = _call_with_retries(
                    lambda: _ollama_search(args, query=query, limit=limit),
                    attempts=retry_attempts,
                    delay_seconds=retry_delay_seconds,
                )
            else:
                raise ValueError(f"Unknown web_search provider: {candidate}")
            result["cached"] = False
            _web_cache_put(workspace, cache_key, result)
            return result
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
            if provider != "auto":
                break
    return {
        "error": "web_search failed",
        "query": query,
        "provider": provider,
        "errors": errors,
        "results": [],
    }


def docs_search(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    query = _require_str(args, "query")
    domain = str(args.get("domain", "")).strip()
    limit = max(1, min(int(args.get("limit", 5)), 10))
    docs_query = f"site:{domain} {query}" if domain else f"{query} documentation docs"
    search_result = web_search(
        workspace,
        {
            "query": docs_query,
            "limit": limit,
            "provider": args.get("provider", "auto"),
            "language": args.get("language"),
            "time_range": args.get("time_range"),
            "timeout": args.get("timeout", 30),
            "ttl_seconds": args.get("ttl_seconds", 3600),
            "retry_attempts": args.get("retry_attempts", 5),
            "retry_delay_seconds": args.get("retry_delay_seconds", 0.25),
        },
    )
    fetched: list[dict[str, Any]] = []
    if bool(args.get("fetch_results", False)):
        max_fetch = max(1, min(int(args.get("max_fetch", 2)), 5))
        for item in search_result.get("results", [])[:max_fetch]:
            url = item.get("url")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                fetched.append(
                    web_fetch(
                        workspace,
                        {
                            "url": url,
                            "max_chars": args.get("max_chars", 6000),
                            "timeout": args.get("timeout", 30),
                            "retry_attempts": args.get("retry_attempts", 5),
                            "retry_delay_seconds": args.get("retry_delay_seconds", 0.25),
                        },
                    )
                )
    return {
        "query": query,
        "docs_query": docs_query,
        "domain": domain,
        "search": search_result,
        "fetched": fetched,
    }
