# Чтение по агентским кодовым системам (краткая карта)

Список ориентиров для локальных агентов на Ollama: индексация, RAG, поиск в интернете, графы знаний, планирование, циклы разработки. Ссылки — отправные точки, не исчерпывающий обзор.

## Официальная документация Ollama (локальная копия)

В репозитории сохранён срез страниц под `docs/references/ollama/` (в т.ч. `llms.txt` — полный индекс). Актуальные URL:

- [Tool calling](https://docs.ollama.com/capabilities/tool-calling)
- [Web search и web fetch](https://docs.ollama.com/capabilities/web-search) (облачный API Ollama, ключ, пример агента с циклом `chat` + тулы)
- [OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [Интеграции OpenCode / Claude Code](https://docs.ollama.com/integrations/opencode), [Claude Code](https://docs.ollama.com/integrations/claude-code)
- [Каталог MCP-серверов (community)](https://github.com/punkpeye/awesome-mcp-servers)

## MCP: что клонировать локально (`thirdparty/mcp/`)

Каталог `thirdparty/` в `.gitignore` — клоны только на машине. Рекомендуемый набор для **кода** и **поиска**:

| Репозиторий | Зачем |
|-------------|--------|
| [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) | Эталонные серверы (filesystem, git и др.) |
| [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) | Клиент MCP на Python для будущей интеграции в `ToolRegistry` |
| [brave/brave-search-mcp-server](https://github.com/brave/brave-search-mcp-server) | Веб-поиск через Brave API (нужен ключ) |
| Опционально: [spences10/mcp-omnisearch](https://github.com/spences10/mcp-omnisearch) | Несколько поисковых бэкендов в одном сервере |

Уже есть в `thirdparty/` (вне `mcp/`): **OpenCode**, **claude-code**, **open-webui** — референсы UI/UX и сценариев.

## Статьи и обзоры (агенты, RAG, репозиторий)

- [A Survey on Code Generation with LLM-based Agents](https://arxiv.org/pdf/2508.00083) — обзор агентов для генерации кода (2025).
- [ARCS: Agentic Retrieval-Augmented Code Synthesis](https://arxiv.org/html/2504.20434v2) — цикл retrieve → synthesize → execute → repair.
- Обзор retrieval-augmented code generation (репозиторный уровень): см. также свежие arXiv по запросу *repository-level code generation RAG survey*.
- RANGER / графы на репозитории: материалы на [OpenReview](https://openreview.net/) и смежные работы по *graph-enhanced code retrieval* (см. выдачу по ключевым словам).
- [CodeRAG-Bench (NAACL 2025 findings)](https://aclanthology.org/2025.findings-naacl.176/) — бенчмарк RAG для кода.

## Графы знаний из веба

Тема шире MCP: обычно цепочка **fetch → извлечение сущностей (LLM/NLP) → граф (RDF, property graph) → запросы**. Имеет смысл смотреть:

- связку **web_fetch** (Ollama) или MCP **fetch/scrape** + отдельное хранилище графа (Neo4j, Kuzu, SQLite с рёбрами);
- исследования по *knowledge graph construction from web corpora* и *Open Information Extraction*.

## Планирование и циклы разработки

- ReAct / tool-calling циклы (общая схема «мысль → действие → наблюдение») — база для вашего `AgentCore` loop.
- Для «плана → патчи → тесты» см. практики **SWE-bench**-подобных агентов и open-source coding agents (OpenCode, кодовые части Claude Code docs).
- Ollama + Claude Code: в доке упоминаются `/loop`, web search, большой контекст — см. локальный файл `docs/references/ollama/integrations/claude-code.md`.

## Инструменты в этом репозитории

- Логи: `InferenceLogger` → `.codeagents/inference.jsonl`, `AuditLog` → `audit.jsonl`, HTTP → `service_requests.jsonl`, запросы к рантайму → `runtime_log`.
- Снимок диска / процессов Ollama / NVIDIA: **GET `/metrics/resources`** (см. `src/codeagents/resource_metrics.py`).

Полноценный GUI «как OpenCode / Claude Code» — отдельный крупный слой (Tauri/Electron/Web + стрим NDJSON); текущий **Rust TUI** и расширение diff — задел под тот же бэкенд.
