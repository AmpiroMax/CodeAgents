---
title: "Deep Research — design notes (Phase 2)"
sources:
  - https://arxiv.org/abs/2506.12594  # Xu & Peng — Comprehensive survey of Deep Research
  - https://arxiv.org/abs/2508.12752  # Zhang et al. — Survey of Autonomous Research Agents
  - https://arxiv.org/abs/2504.21776  # WebThinker — Deep Research with LRMs
  - https://github.com/LearningCircuit/local-deep-research
  - https://habr.com/ru/articles/923948/
---

# Deep Research — что выжимаем из материалов

## 1. Каноничный пайплайн (Zhang 2025, §1; Xu 2025, §2)

Все современные deep-research системы сводятся к четырём фазам:

```
Planning  →  Question Developing  →  Web Exploration  →  Report Generation
   ↑                                       │
   └────── feedback loop (re-plan) ────────┘
```

- **Planning** — превратить вопрос пользователя в дерево/список под-целей
  (sub-goals). Часто структурированно: `outline → sections → questions`.
  Подходы: world-model simulation (WebDreamer), modular plans (Plan-and-Act),
  iterative outline (STORM).
- **Question Developing** — для каждого sub-goal сгенерировать N
  поисковых запросов разной формулировки (synonym/expand/decompose).
- **Web Exploration** — итеративно: search → выбрать source → fetch →
  parse → решить «достаточно ли»; кликать по ссылкам (WebThinker
  Deep-Web-Explorer).
- **Report Generation** — собрать структурированный отчёт со ссылками
  на источники; либо «всё разом», либо section-by-section
  (Think-Search-and-Draft).

## 2. Что берём в CodeAgents

| Идея                                | Откуда                          | Куда у нас             |
|-------------------------------------|---------------------------------|------------------------|
| 4-фазный конечный автомат           | Zhang 2025                      | `mode=research` FSM    |
| Outline-first report                | STORM, WebThinker               | план как outline → запросы |
| Think-Search-and-Draft (interleaved) | WebThinker §3                  | tool `draft_section`   |
| Деревовидный (а не линейный) поиск  | habr/aifa                       | sub-goal tree, BFS     |
| Citations-first (each claim → source) | Perplexity / LDR              | обязательные `[n]` маркеры |
| Adaptive query reformulation        | LDR, WebThinker                 | `expand_query` тул     |
| Iterative termination criterion     | LDR `focused-iteration`         | budget + entropy stop  |
| Per-source quality scoring          | LDR (OpenAlex, predatory list)  | trust score для report |
| Local-only stack (Ollama+SearXNG)   | LDR                             | у нас уже есть         |

## 3. Termination / budget (важно для нашей токен-проблемы)

LDR использует две стратегии остановки:

- **focused-iteration** — фиксированное число итераций (например, 3 или 5).
  Каждая итерация: новый search → fetch top-k → дописать в notes.
- **iterdrag** — останавливается, когда «marginal information gain» падает
  (новые источники не добавляют новых фактов).

Для нас более практично — `max_iters` + `max_tokens_in_notes` + ранняя
остановка по «пустому шагу» (модель явно говорит `no_new_info=true`).

## 4. KG в контексте deep research (NB: не самоцель)

Survey (Zhang 2025) явно отмечает: **граф знаний в deep-research — это
вторичная структура** для:

1. **Дедупликации фактов** между источниками (entity resolution).
2. **Cross-source reasoning** — сводить противоречащие утверждения
   (две статьи говорят разное про один tool — выбрать более надёжный).
3. **Cite-graph** — граф «откуда что взяли» для финального отчёта.

LightRAG / GraphRAG / HippoRAG хорошо ложатся именно как **слой над
notes**, а не как отдельный режим. Поэтому KG в Phase 2 встраивается
**внутрь** deep-research петли, а не как параллельный mode.

## 5. Ключевые тулы, которые нужны (по Zhang 2025 §3-5)

- `plan_research(query) → outline+subgoals`
- `expand_query(subgoal) → [q1, q2, ...]`
- `web_search(q)`            ✓ уже есть
- `web_fetch(url)`           ✓ уже есть
- `extract_facts(url, text) → [{claim, source, span}]`  — новый
- `kg_add({claim, source})`, `kg_query(entity)` — опционально (Phase 2.B)
- `draft_section(title, facts) → markdown` — новый
- `revise_report(report, critique) → markdown` — новый
- `recall_chat`              ✓ уже есть
- `search_code`              ✓ уже есть

## 6. Token budget (наша sub-задача)

Ollama в `/api/chat` стриминге уже отдаёт точные счётчики
(`prompt_eval_count`, `eval_count`) — мы уже их пишем в инференс-лог.
Поэтому *post-hoc* счёт — точный.

**Pre-hoc** (до запроса) — оценка, иначе непонятно сколько подкладывать
recall'а. Варианты:

1. **Tiktoken `cl100k_base`** для большинства open моделей. Грубо, но в
   пределах ±10%.
2. **HF tokenizer соответствующей модели** — точно, но dependency.
3. **Ollama `/api/show <model> → tokenizer.ggml.tokens`** — есть, можно
   распарсить. Опять же грубо.
4. **Калибровка**: считать char-эвристикой, поправлять EMA по реальным
   `prompt_eval_count` за последние N турнов на эту модель.

Наш план — комбинировать (1)+(4): tiktoken по умолчанию, с поправкой
от реального usage. Это переименует нашу метрику `chars`-based в
`tokens`-based и закроет вопрос пользователя.
