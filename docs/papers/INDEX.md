# Reading list — RAG, knowledge graphs, deep-research agents

Курированный список статей, репозиториев и форум-материалов, на которые
будем опираться при реализации фич **4.1.3 (RAG agents)**, **4.2.3
(search agent + KG + deep-research)** из `feature_requests.md`.

Источники сгруппированы по задачам. Для каждой записи: **что даёт**, **что
взять для CodeAgents**, ссылка на arxiv / репо. Где репозиторий полезен
целиком — добавил в `thirdparty/papers/CLONE.sh`.

---

## A. RAG over conversational memory (1.4 — chat-RAG)

### A.1. MemGPT — virtual context for long chats *(Berkeley, 2023)*
- arxiv: <https://arxiv.org/abs/2310.08560>
- repo: <https://github.com/cpacker/MemGPT> (now Letta)
- **Идея:** делит память на «main context» (то что в окне) и «archival storage»
  (внешняя БД), агент сам решает когда позвать `archival_memory_search` /
  `archival_memory_insert`. Бюджет main-context жёстко контролируется.
- **Что забрать:** именно ту схему «active recall = функция в наборе тулов».
  Ровно то, что мы сделали с `recall_chat`. У них же — формула auto-eviction
  при превышении бюджета (`heartbeat events`), это шаблон для Phase 2.

### A.2. RAGEval — оценка chat-RAG *(2024)*
- arxiv: <https://arxiv.org/abs/2408.01262>
- **Идея:** методика как мерить полезность recall — F1 по «помнит ли агент
  факт X из k-турна назад».
- **Что забрать:** простой бенч для нашего `recall_chat` чтобы сравнивать
  чанки 20-line vs 40-line, top-k=3 vs k=5.

### A.3. CompressAgent / LongRoPE / Activation Beacon
- <https://arxiv.org/abs/2401.03462> (Activation Beacon)
- **Идея:** сжимать длинный контекст не суммаризацией а через learnable
  beacon-токены.
- **Релевантность:** низкая для нас (нужна тренировка модели), но полезно
  знать как альтернатива «summarize-old».

---

## B. RAG over codebases (4.1.2 — semantic code search)

### B.1. Repo-level prompt generation / RepoCoder *(Microsoft, 2023)*
- arxiv: <https://arxiv.org/abs/2303.12570>
- repo: <https://github.com/microsoft/CodeT/tree/main/RepoCoder>
- **Идея:** iterative retrieval — сначала эмбед-поиск, потом «pseudo-completion»
  от модели, потом второй retrieval по результату. Повышает recall на 30-40%.
- **Что забрать:** второй проход для `search_code`, когда первый return был
  слабый (score < 0.3 у топ-1).

### B.2. CodeRAG-Bench *(2024)*
- arxiv: <https://arxiv.org/abs/2406.14497>
- **Идея:** какие чанк-стратегии лучше для кода — AST-based vs sliding window
  vs file-level. Их вывод: **AST + 20-line sliding окно** даёт лучший F1
  (мы уже так и делаем после эпика D — это не случайность).

### B.3. CodePlan / Multi-file edit *(2024)*
- arxiv: <https://arxiv.org/abs/2309.12499>
- **Идея:** агент сначала строит plan-граф по зависимостям файлов, потом
  правит по узлам в нужном порядке.
- **Что забрать:** имеет смысл для будущей фичи «refactor across files»,
  привязать к нашему plan subsystem.

---

## C. Knowledge graphs from text (4.2.3 — KG)

### C.1. **GraphRAG** *(Microsoft Research, 2024)*
- arxiv: <https://arxiv.org/abs/2404.16130>
- repo: <https://github.com/microsoft/graphrag> (Python, MIT)
- **Идея:** LLM извлекает entities + relationships по чанкам → строит KG →
  Leiden-кластеризация → community summaries → запрос идёт по
  community-summary иерархии.
- **Что забрать:** структура схемы (entities, relationships, communities),
  сам пайплайн extract→cluster→summarize. Их код тяжёлый (Azure-centric),
  но `graphrag.index.operations.extract_entities` напрямую полезен как
  reference.

### C.2. **LightRAG** *(HKUDS, 2024)*
- arxiv: <https://arxiv.org/abs/2410.05779>
- repo: <https://github.com/HKUDS/LightRAG> (Python, MIT, ~3 kLOC)
- **Идея:** упрощённая версия GraphRAG. Один граф вместо иерархии, две
  схемы извлечения (low-level entity, high-level theme), retrieval идёт
  по обоим уровням.
- **Что забрать:** код реально читаемый, сделано на networkx + nano-graphrag.
  **Это то, на что я бы опирался для CodeAgents** — мало зависимостей,
  легко поднять локально без Azure.

### C.3. **HippoRAG** *(Stanford+OSU, NeurIPS 2024)*
- arxiv: <https://arxiv.org/abs/2405.14831>
- repo: <https://github.com/OSU-NLP-Group/HippoRAG>
- **Идея:** Personalized PageRank по графу + dense retrieval. Лучше работает
  на multi-hop вопросах («какой автор писал статью на которую ссылается X»).
- **Что забрать:** именно PPR-ходок по KG как post-retrieval. Реализация
  — на networkx, прозрачная.

### C.4. **Triplex** *(SciPhi, 2024)*
- blog: <https://huggingface.co/blog/sciphi/triplex>
- model: `sciphi/triplex` на HuggingFace
- **Идея:** маленькая 3B-модель специально обучена выкидывать `(subj, rel, obj)`
  тройки из текста. Дешевле и точнее чем общие LLM для extraction.
- **Что забрать:** для search-агента, который будет много читать —
  использовать triplex для extraction вместо тяжёлых LLM-промптов.

---

## D. Deep-research / autonomous search agents (4.2.3 — search mode)

### D.1. **STORM** *(Stanford, NAACL 2024)*
- arxiv: <https://arxiv.org/abs/2402.14207>
- repo: <https://github.com/stanford-oval/storm> (Python, MIT)
- **Идея:** 2-stage pipeline для написания «википедийных» статей по теме —
  (1) multi-perspective question generation, (2) retrieval+writing с outline.
- **Что забрать:** именно outline-driven writing — search-агент сначала
  должен сделать **план разделов отчёта**, потом по каждому отдельный
  retrieval. Это укладывается на нашу plan-subsystem.

### D.2. **Search-R1 / DeepResearcher** *(2024-25)*
- arxiv (Search-R1): <https://arxiv.org/abs/2503.09516>
- arxiv (DeepResearcher): <https://arxiv.org/abs/2504.03160>
- **Идея:** RL-обученный агент с инструментами `web_search` + `web_browse`
  + `python`. Награда — точность финального ответа на multi-hop QA.
- **Что забрать:** сам набор тулов и формат «think → search → reflect →
  search → answer». В CodeAgents этот цикл уже есть, нужно лишь добавить
  `python` для аналитики (фичреквест 4.2.4).

### D.3. **OpenAI Deep Research** (продакт, февраль 2025)
- blog: <https://openai.com/index/introducing-deep-research/>
- **Идея:** агент 5-30 минут гуляет по интернету, читает 100+ страниц,
  возвращает отчёт со ссылками. Технически — o3-серии модель в режиме
  long-horizon planning + browsing.
- **Что забрать:** UX — пока агент работает, юзеру показывают **нумерованный
  лог шагов** («Read X about Y», «Fetched Z», …). Полезно для нашего
  search-mode UI.

### D.4. **Reflexion + ReAct + Self-Ask** *(foundational)*
- ReAct: <https://arxiv.org/abs/2210.03629>
- Reflexion: <https://arxiv.org/abs/2303.11366>
- Self-Ask: <https://arxiv.org/abs/2210.03350>
- **Идея:** базовые петли «mind→act», self-critique после неудачного
  retrieval, multi-hop разбивка вопроса.
- **Релевантность:** база — все современные deep-research агенты это
  расширяют.

### D.5. **AutoGen / LangGraph patterns** *(2024)*
- AutoGen: <https://github.com/microsoft/autogen>
- LangGraph: <https://github.com/langchain-ai/langgraph>
- **Идея:** state-machine'ы для агентов с явными узлами retrieval/think/act.
- **Что забрать:** не сам код (тяжёлый), а паттерны узлов и edge-conditions
  для нашей будущей plan-driven search-loop.

---

## E. Forum / community discussions

- **Reddit r/LocalLLaMA — best embedding for code (2024-25):**
  <https://www.reddit.com/r/LocalLLaMA/search/?q=embedding+code> —
  тред'ы где раскатывают `embeddinggemma`, `nomic-embed-text-v1.5`,
  `bge-m3`. Вердикт: для русско-английского кода gemma-300m выигрывает,
  для чисто английского — bge-m3 чуть лучше.
- **HN thread on GraphRAG vs LightRAG (Oct 2024):**
  <https://news.ycombinator.com/item?id=41880515> — практические замеры,
  что LightRAG в 5-10x дешевле в build, качество сопоставимое на small-corpus.
- **Ollama discord #embeddings** — у Ollama теперь свой `embeddinggemma:300m`
  endpoint работает быстрее nomic на ARM, см. <https://ollama.com/library/embeddinggemma>.

---

## Что делаем дальше

Phase 2 эпики, которые опираются на этот список:

- **P2-A (chat-RAG автоподстановка):** MemGPT (A.1) + recall budget formula
  из RAGEval (A.2). Спроектировать как «hidden system block» с top-3 hits
  на каждый user-турн.
- **P2-B (KG для search-mode):** LightRAG (C.2) как ядро + Triplex (C.4) для
  extraction. Постепенно — иерархия как в GraphRAG (C.1) если потребуется
  scale.
- **P2-C (deep-research mode):** STORM (D.1) для outline-driven отчётов +
  Search-R1 (D.2) для архитектуры agent-loop.
- **P2-D (PDF/vision):** см. фичреквесты 4.2.2 и 5.3.

См. также `thirdparty/papers/CLONE.sh` — скрипт для shallow-clone'а
LightRAG/HippoRAG/STORM/GraphRAG в `thirdparty/papers/<repo>/`.
