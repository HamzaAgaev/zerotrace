# Changelog

Все заметные изменения бэкенда ZeroTrace фиксируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
проект придерживается [Semantic Versioning](https://semver.org/lang/ru/).

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
