# server

**Зачем:** сборка единого HTTP-приложения (Flask), lifecycle и раздача UI.
Зона A (Никита); файл `assembly.py` — совместный стык с B.

**Модули:**

- `application.py` — `create_app(config)`: поднимает SpanStore (B) и
  ControlStore, реестр источников, лабораторию, аналитику и все HTTP-маршруты
  (OTLP, Product API, data API, аналитика, статика фронтенда). Один процесс —
  один SQLite-файл в WAL.
- `assembly.py` — `AssemblyTicker`: фоновый daemon-поток, раз в секунду
  вызывает `SpanStore.advance_assembly` (open → quiet для молчащих трасс,
  окно 5 с; обе настройки через config: `assembly_tick_s`,
  `assembly_quiet_after_ns`). Цикл сам завершается при закрытии стора;
  `stop()` — явная остановка.

**Запуск:** `python -m app.server --config deploy/config.json`
(см. `docs/guides/operations.md`).
