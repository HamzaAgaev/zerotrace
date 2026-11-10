# Changelog

Все заметные изменения бэкенда ZeroTrace фиксируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
проект придерживается [Semantic Versioning](https://semver.org/lang/ru/).

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
