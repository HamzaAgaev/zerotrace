# Changelog

Все заметные изменения бэкенда ZeroTrace фиксируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
проект придерживается [Semantic Versioning](https://semver.org/lang/ru/).

## [Unreleased]

## [Unreleased]

- Golden-харнесс для миграции аналитики на `core/model.py`:
  `tests/analytics/test_migration_golden.py` фиксирует полный вывод
  `rules.audit.run()` на неизменном входе (`fixtures/analytics/golden/audit-baseline.json`).
  Обновление только через `GOLDEN_UPDATE=1` с ревью диффа. План миграции:
  `docs/development/analytics-core-model-migration.md`.
- Канонические примитивы доверия и арифметики перенесены из `rules/budget.py`
  в `core/model.py` (`sourced_value`, `is_current_policy`, `pauses_ns`,
  `integer` — точные порты, golden побайтово зелёный). `rules/budget.py`
  делегирует через прежние имена; поведение аудита не изменилось.
  Тесты `TestTrustPrimitives` фиксируют согласованность типизированного
  `EdgePolicy` с примитивами и одно сознательно сохранённое расхождение
  (плоское чтение независимо от свежести vs понижение доверия typed-вида).

## [0.6.1] — 2026-11-11 21:23 МСК

Причина принудительного закрытия трассы доходит до графа; чеклист демо.

### Изменено

- `build_graph(..., forced_reason=...)`: трасса в состоянии `forced` получает
  quality-issue `forced_close` с причиной (раньше причина хранилась в
  `TraceMeta`, но граф её терял). Прокинута через `app/data_api/adapter.py`
  и `app/analytics_api/repository_source.py`.

### Документация

- `docs/guides/demo-checklist.md`: пошаговый сценарий показа (подготовка,
  shop → стенд → fault → прогоны → план → сравнение) с ожидаемыми значениями
  и запретами на показе. Проверен на живом compose-стенде.
- `HANDOFF.md`: актуальные команды проверок и ссылка на чеклист.

### Тесты

- 403 passed (+3): `forced_close` в графе (с причиной и без), отсутствие
  issue для open/quiet, сквозной путь store → adapter → graph.

## [0.6.0] — 2026-11-11 21:13 МСК

B04 (M2): типизированная per-edge модель P3 — статистика и политики.

### Добавлено

- `core/model.py`:
  - `EdgeContribution` (пара TraceGraph+CallModel одной трассы) и
    `build_edge_stats(contributions)` → `dict[edge_id, EdgeStats]` по P3:
    attempts/eligible/ambiguous calls, outcomes (known/unknown/errors),
    coverage, attempt_error_ratio, success p50/p95/p99 (контрактный
    перцентиль), gaps_by_index, jitter_cv. Ребро без попыток не создаётся —
    no samples означает null, не ноль.
  - `build_edge_policy(flat_config)` → `EdgePolicy`: подъём плоского снимка
    конфигурации A в типы P3. ms → ns, `Sourced`-операнды с
    config_revision, stale/binding_conflict понижают доверие,
    `effective_source` = fresh_runtime | declared | cautious_inference,
    явный внешний `budget_ns` биндится только как declared.
  - `build_model(contributions, policies, known_edges)` → `ModelView`:
    статистика + политики по edge_id, idle рёбра из known_edges остаются
    на графе со stats=None. `dataset_ref` — дайджест входной идентичности.
- Экспорты в `core/__init__.py`, описание в `core/README.md`.

### Тесты

- 400 passed (+18): агрегация по s00/s01 fixtures, ambiguous-вызовы не
  eligible, слияние вкладов по трассам, стабильность dataset_ref, переходы
  effective_source (fresh/stale/conflict/declared/budget), idle-рёбра,
  JSON-roundtrip ModelView.

## [0.5.0] — 2026-11-11 21:05 МСК

Чистка и верификация интегрированного дерева после слияния частей A/C/D.

### Изменено

- Ruff-совместимость зон A/C в общем дереве: сортировка импортов
  (`app/data_api/adapter.py`), форматирование `app/data_api/handlers.py`,
  `app/data_api/views.py`, `contracts/p7.py`, `repository/store.py`.
- `app/server/application.py`: устранён переполнение строки (E501) в логике
  пропуска config-снимков для чтений без аудита.

### Документация

- `HANDOFF.md`: бриф для коллег — что готово в data-слое, как запускать
  проверки, на какие решения опираться.

### Тесты

- 382 passed + 93 сабтеста (полный охват `backend/tests` + `stand/tests`),
  фронтенд 37/37. Проверен сквозной демо-сценарий на compose-стенде:
  fault → аудит R4 → план по рекомендации → применение → повторный прогон →
  сравнение (attempts_per_input 6.0 → 4.0 при неизменных ошибочных входах),
  находка R4 после исправления исчезает.

## [0.4.0] — 2026-11-11 20:00 МСК

Интеграция командных частей в общий сервер и живой стенд.

### Добавлено

- Чтение аналитики работает с отдельным снимком SQLite WAL и освобождает запись телеметрии на время расчёта.
- Список и детали трейса поддерживают `include_findings=false` для чтения без аудита и обращений к управляющим адаптерам.
- Повторные запросы используют ограниченные кэши производных данных и аудита по точным ревизиям. Параллельные запросы с одинаковым входом делят один расчёт аудита.
- Для холодного аудита добавлен фоновый режим с ответом `202` и опросом по идентификатору задачи.
- Список сериализует только выбранную страницу, а `capabilities` определяет свежесть по индексированному времени последнего приёма.

## [0.3.0] — 2026-11-11 03:10 МСК

Стабильность ключей, жизненный цикл трасс, product fixtures для фронтенда.

### Добавлено

- `NormalizedSpan.namespace` (аддитивно; источник истины — resource attrs
  на этапе normalize, дефолт `"default"`).
- `derive_calls(graph, project_id=...)` — реальные project/namespace в
  `ServiceKey`: `edge_id` больше не зависит от дефолтов и совпадает у B и C.
  Namespace берётся из спана, project передаётся явным параметром.
- Жизненный цикл `assembly_state`: `SpanStore.advance_assembly(now,
  quiet_after_ns=5с)` переводит молчащие «open»-трассы в `quiet`
  (idempotent, bounded-скан); `SpanStore.force_close(ref, reason)` —
  принудительное закрытие по лимиту с записью причины. Миграция схемы
  v1/v2 → v3 (`traces.forced_reason`), причина читается через `TraceMeta`.
- `fixtures/build_product_fixtures.py` + `fixtures/product/`: реальные
  ответы data API (trace-list и trace-detail для окружений shop и sandbox),
  собранные сквозным прогоном OTLP → SQLite → handlers с фиксированным
  `generated_at` — контрактные примеры для фронтенда (B08).

### Тесты

181 passed (+11): обогащение ключей (project/namespace меняют и стабильно
дают edge_id), жизненный цикл assembly (quiet, forced + reason,
idempotentность, миграция со старой схемы), парсинг product fixtures.

## [0.2.0] — 2026-11-11 02:53 МСК

Стыки первого показа: цепочка для стенда, выборки прогонов, приём OTLP.

### Добавлено

- `core/chain.py`: `extract_chain(graph, calls)` — проверенная последовательная
  цепочка для генератора стенда (стык B → A) либо типизированный отказ
  (empty_trace / ambiguous_root / missing_link / ambiguous_link /
  parallel_branches / cycle) с конкретными проблемными связями. Внешний
  адресат без server-спана допустим последним шагом; повторные попытки
  одного logical call не размножают звенья; признаки неполноты передаются
  в `incompleteness`.
- Фиксация выборок прогонов (стык A → B → C): `normalize` извлекает
  lab-метки `zerotrace.run_id`/`run_id` и `zerotrace.probe`/`lab.probe`;
  `repository` хранит `trace_runs` и `run_samples`.
  `start_collection` (идемпотентно), `finalize_collection` /
  `get_sample` → статус waiting/ready/partial с expected/received counts
  и замороженными точными TraceRef; повторное чтение после фиксации
  возвращает тот же набор, поздние трассы выборку не меняют;
  probe-трассы исключены.
- `app/otlp_api/`: ручка приёма `POST /v1/traces` — Bearer-токен →
  SourceBinding (401 на неизвестный), decode → normalize → транзакционная
  запись; 200 только после commit, сбой записи → 503 без ложного ACK;
  ответ ExportTraceServiceResponse в формате запроса.
- Fixtures: `target` на клиентских спанах model-фикстур (согласовано с
  OTLP-фикстурами, где callee виден через `server.address`).

### Исправлено

- Deadlock в `SpanStore`: вложенные чтения под общим замком (Lock → RLock).

### Тесты

170 passed (+21): цепочка (happy path, отказы, двухзвенная цепочка),
выборки прогонов (waiting→ready, partial, freeze-стабильность, probe,
конфликт параметров), OTLP handler (protobuf/JSON приём, 401/413/415/503,
дубликат без двойного счёта).

## [0.1.0] — 2026-11-11 01:15 МСК

Первый рабочий срез: контракты данных и сквозной конвейер приёма OTLP.

### Добавлено

**Контракты (`contracts/`)** — этап B01:

- Базовые типы идентичности: `ServiceKey`, `OperationKey`, `TraceRef`
  (окружение + trace ID + generation + revision), `EndpointKey`
  (service/external/db/unknown), `EdgeKey` со стабильным `edge_id`
  (SHA-256 канонического JSON, версионированная canonicalization `v1`).
- P1: `IngestLimits` (8 МиБ body, 10 000 spans/пакет, 2 000 spans/trace,
  глубина 256), `SourceCapabilities`, `SourceBinding`.
- P2: `NormalizedSpan`, `TraceGraph`, `Quality` с реестром кодов, `Attempt`,
  `LogicalCall`, `CallModel`. Unknown — `None`, никогда не 0/false.
- P3: `Sourced<T>` с provenance значения, `EdgePolicy`, `EdgeStats`,
  перцентили по контракту (linear interpolation, округление half-up к ns).
- P7 view-DTO для data API: `TraceSummary`, `SpanView`, `TraceDetail`,
  `Envelope`. `*_ns` сериализуются десятичными строками.
- Валидатор: hex ID (32/16, ненулевые, lowercase), end ≥ start,
  duration = end − start; отсутствие `service.name` — предупреждение
  о coverage, не ошибка приёма.

**Конвейер данных** — этап B02:

- `ingest/decoder.py`: OTLP/HTTP protobuf и JSON, gzip с лимитом
  распаковки. Типизированные ошибки с HTTP-кодами: 400 (невалидные
  ID/времена), 413 (размер), 415 (media/encoding).
- `ingest/normalize.py`: resource attrs → service/instance/environment,
  HTTP route/status, error.type, redaction (query/секреты не хранятся,
  идентификаторы в путях → placeholder с provenance), `field_coverage`.
- `repository/`: SQLite (WAL, synchronous=FULL). Транзакция на пакет,
  ошибка записи → откат без ложного ACK. Повтор пакета не меняет
  revision/счётчики; поздний спан → revision+1; конфликт спанов →
  diagnostic, хранится первый принятый. TTL отсчитывается от
  first_received и не продлевается повторами. `resolve_trace`,
  `list_trace_metas`, удаление с ожидаемой ревизией.
- `core/graph.py`: индексы parent/children, сортировка (start, end, id),
  итеративная проверка циклов, depth limit, quality-коды
  (missing_parent, multiple_roots, cross_clock_uncertain и др.).
- `core/calls.py`: CLIENT → `Attempt`, outcome по правилам контракта
  (отсутствие ERROR не доказывает ok), группировка в `LogicalCall`:
  явные маркеры сильнее эвристики «ошибка → следующий вызов»,
  overlap → ambiguous, пять успешных вызовов — пять single.
- `core/timing.py`: union интервалов, раздельные показатели
  uncovered_direct_children / uncovered_outgoing_calls, offsets только
  на совместимых часах, p50/p95/p99 через контрактный перцентиль.
- `app/data_api/`: обработчики P7 без привязки к фреймворку —
  `list_traces` (сортировка first_received DESC, limit ≤ 100),
  `get_trace` (400 на неполную пару версий, 404 на недоступный ref),
  адаптер к `SpanStore`.

**Fixtures и тесты**:

- `fixtures/model/` (s00_healthy, s01_retry) и `fixtures/otlp/` —
  генерация скриптами, контрольные числа совпадают с планом
  (3 × 250 мс + 2 × 50 мс = 850 мс).
- 149 тестов: контракты, совместимость с fixture фронтенда
  (`frontend/fixtures/frontend-contracts.json`), ingest, repository,
  graph/calls/timing, data API, сквозной конвейер
  «декод → нормализация → SQLite → API».

**Инструменты**: ruff (check + format), mypy strict для `contracts`,
pytest, venv в `backend/.venv`; команды проверки — в `README.md`.

### Известные ограничения среза

- `assembly_state` всегда `open` (quiet/forced — этап B03).
- `generation` всегда 1 (TTL/budget — этап B05).
- `finding_count` в data API всегда 0 (находки вычисляет аудит — зона C).
- `derive_calls` использует project/namespace `default` до согласования
  источника реальных ключей.
