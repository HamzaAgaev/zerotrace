# tests

Приёмочные проверки серверных модулей. Команды запуска в Docker — в
[руководстве по проверкам](../../docs/development/testing.md).

- Корневые `test_*.py` проверяют контракты, ingest, core, repository и data API.
- `control/` проверяет управление конфигурациями и восстановление.
- `analytics/` проверяет правила, рекомендации и аналитические адаптеры.
- `integration/` проверяет общий сервер с реальными HTTP-процессами.

**Карта тестов (по файлам):**

| Файл | Что покрывает |
|---|---|
| `test_contracts.py` | Типы P1–P3: edge_id, валидация, roundtrip JSON, ns-строки, перцентили. |
| `test_frontend_compat.py` | Парсинг fixture фронтенда (`frontend/fixtures/frontend-contracts.json`) в контрактные типы. |
| `test_ingest.py` | Decoder/normalize: protobuf↔JSON, лимиты, redaction, ns roundtrip. |
| `test_repository.py` | SQLite: дубликаты, поздние спаны, конфликты, откат, перезапуск, удаление. |
| `test_core_graph.py` | Граф: циклы, глубина, сортировка, качество. |
| `test_core_calls.py` | Попытки и группировка: single/retry/ambiguous. |
| `test_core_timing.py` | Union интервалов, uncovered, перцентили, часы. |
| `test_core_chain.py` | Извлечение цепочки для стенда: happy path и отказы. |
| `test_core_model.py` | Per-edge P3 модель: EdgeStats, EdgePolicy, trusted-примитивы. |
| `test_assembly.py` | Жизненный цикл трасс (quiet/forced) и фоновый AssemblyTicker. |
| `test_migration_golden.py` | Golden-снимки вывода аудита (миграция C → core/model). |
| `test_data_api.py` | P7 handlers: envelope, пагинация, 400/404. |
| `test_otlp_handler.py` | Приём OTLP: protobuf/JSON, 401/413/415/503, дубликаты. |
| `test_run_samples.py` | Выборки прогонов: waiting/ready/partial, freeze, probe. |
| `test_pipeline_integration.py` | Сквозной путь: декод фикстуры → SQLite → API-ответ. |

`backend/conftest.py` добавляет `backend/` в `sys.path`, чтобы pytest мог
проверять исходники без установки пакета. Docker также задаёт Python path
для серверных пакетов и отдельного исполнителя `stand`.
