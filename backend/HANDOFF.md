# Передача коллегам: состояние зоны B (данные/Core)

От Миши (B), 2026-11-11. Бэкенд данных готов к интеграции M1: приём OTLP,
хранение, граф, попытки, цепочка для стенда, выборки прогонов, data API.
181 тест зелёный, ruff/mypy чистые. История изменений — `CHANGELOG.md`.

## Никите (A): подключение сервера и стенда

1. **OTLP-приём** — `app/otlp_api/handler.py`:
   `handle_traces(store, token_registry, body, headers, limits, received_at_ns)`
   → `OtlpResponse{status, body, media_type}`. От тебя нужен `TokenRegistry`
   (Protocol: `resolve(token) -> SourceBinding|None`) — реестр токенов из
   P11. Коды: 401 неизвестный токен, 400/413/415 от decoder'а, 503 сбой
   записи, **200 только после commit** (ложного ACK нет). Ответ —
   настоящий `ExportTraceServiceResponse` в формате запроса.
2. **Data API** — `app/data_api/handlers.py`: `list_traces` / `get_trace`.
   Store-контракт — `TraceStore` Protocol; готовый мост к моему хранилищу —
   `app/data_api/adapter.py` (`SpanStoreAdapter`, `make_graph_builder`).
3. **Цепочка для сборки стенда** — `core.extract_chain(graph, calls)`
   → `ExtractedChain` (упорядоченные звенья: caller/callee, операция,
   наблюдаемый статус, длительность, `incompleteness` для генератора)
   или `ChainRefusal` с кодом (`ambiguous_root`, `missing_link`,
   `ambiguous_link`, `parallel_branches`, `cycle`) и списком проблемных
   связей — под 422 из P11. Внешний адресат без server-спана допустим
   последним шагом.
4. **Выборки прогонов** — `repository.SpanStore`:
   `start_collection(run_id, env, expected_count, deadline)` →
   `finalize_collection(run_id, now)` / `get_sample(run_id)` → статус
   **waiting/ready/partial**, expected/received counts, замороженные точные
   TraceRef. Твой стенд шлёт `zerotrace.run_id` в атрибутах спанов
   (поддержаны оба ключа: `zerotrace.run_id` и `run_id`); пробные запросы
   помечай `zerotrace.probe=true` — они исключены из выборок. Повторное
   чтение после фиксации возвращает тот же набор.
5. **Нужно от тебя:** реальный OTLP-пакет агента и финальные
   токены/идентификаторы окружений — прогоню decoder на живых данных.

## Амиру (C): вход для аудита

1. **CallModel** — `core.derive_calls(graph, project_id="zerotrace-demo")`
   → attempts + LogicalCall: hypothesis single/retry/hedge/ambiguous,
   `gaps_ns`, `first_path` (nullable), outcome по правилам (отсутствие
   ERROR не доказывает ok; CLIENT/SERVER статусы не смешиваются).
2. **Выборки прогонов** — `SpanStore.get_sample(run_id)` → замороженные
   TraceRef; дальше `get_spans(ref)` → `core.build_graph` → `derive_calls`.
3. **edge_id стабилен** — `ServiceKey{project_id, environment, namespace,
   service_name}`. Правило: project передаём одинаковый (`zerotrace-demo`),
   namespace берётся из спанов (`NormalizedSpan.namespace`). Тогда хеши
   рёбер у нас совпадут.
4. **Тайминги** — `core/timing.py`: `union_duration`, два раздельных
   показателя `uncovered_direct_children` / `uncovered_outgoing_calls`
   (не смешивать!), offsets только на совместимых часах, p50/p95/p99 через
   контрактный перцентиль (`contracts.percentile_ns`).
5. **Контрольные числа R4** — `fixtures/model/s01_retry`: 3 попытки ×
   250 мс + 2 паузы × 50 мс = 850 мс; `s00_healthy` — здоровый negative.

## Хамзе (D): контрактные ответы API

1. `fixtures/product/` — реальные envelope'ы `trace-list` и `trace-detail`
   для окружений `shop` и `sandbox`, собранные сквозным прогоном конвейера
   (OTLP → SQLite → handlers), не нарисованы руками. Форма совпадает с
   `frontend/fixtures/frontend-contracts.json`; парсинг твоими типами
   покрыт тестом `tests/test_product_fixtures.py`.
2. **Endpoint'ы первого среза**: `GET /api/v1/traces?environment_id=&
   limit=&offset=` (limit ≤ 100, сортировка first_received DESC) и
   `GET /api/v1/traces/{id}?environment_id=&generation=&revision=`
   (обе версии вместе или ни одной — иначе 400; недоступная ревизия —
   404 `trace_unavailable`).
3. **Перцентили**: сервер считает linear interpolation (P3), не
   nearest-rank — при переходе с локальных агрегатов числа немного
   сдвинутся.
4. `SpanView.start_offset_ns` считается на сервере — фронт timestamps не
   вычитает.

## Всем

- Контракты (`contracts/`) заморожены; изменения — через Мишу с ревью
  потребителей.
- Проверки из корня репозитория: `backend/.venv/bin/python -m pytest backend/tests
  stand/tests -q` (400+ зелёных; в PATH нужен `backend/.venv/bin` для
  интеграционных тестов), `cd backend && .venv/bin/ruff check contracts core
  ingest repository app/control app/integrations app/server app/data_api
  app/otlp_api && .venv/bin/mypy`.
- Зоны B: `ingest/`, `repository/`, `core/`, `app/data_api/`,
  `app/otlp_api/`, `fixtures/`, `tests/` — правки через Мишу.
- У каждого пакета есть `README.md` с кратким описанием модуля.
- Сценарий демо по шагам: `docs/guides/demo-checklist.md` (проверен на
  compose-стенде 2026-11-11).
- План миграции аналитики на `core/model.py` (после демо, зона C):
  `docs/development/analytics-core-model-migration.md`.
