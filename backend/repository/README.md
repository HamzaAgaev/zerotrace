# repository

**Зачем:** единственный stateful-слой данных — хранит спаны на диске в
SQLite и гарантирует целостность конвейера. Зона B (Миша); тот же
интерфейс используют A (журнал) и C (baseline persistence).

**Как устроено:**

- `store.py` — `SpanStore(path)`: одна SQLite-база (WAL,
  synchronous=FULL), миграции схемы в коде (`meta.schema_version`).
  Таблицы: `traces` (метаданные трассы: generation, revision, первый/
  последний приём, assembly_state), `spans` (канонический JSON спана,
  PK = project+trace+generation+span), `trace_runs` и `run_samples`
  (выборки прогонов стенда).
- **Гарантии:** пакет пишется одной транзакцией, ошибка → откат, ложного
  ACK нет; идентичный повтор пакета не меняет revision и счётчики;
  поздний спан дополняет trace и повышает revision; конфликт двух
  завершённых спанов с одним ID → diagnostic, хранится первый принятый;
  TTL отсчитывается от first_received и не продлевается повторами.
- **Интерфейс чтения:** `get_spans(ref)` — точная ревизия, `resolve_trace`
  (обе версии None → текущая), `list_trace_metas` (пагинация для API),
  `delete_trace` с ожидаемой ревизией.
- **Выборки прогонов:** `start_collection` → `finalize_collection` /
  `get_sample`: статус waiting/ready/partial, expected/received counts,
  замороженные точные TraceRef; после фиксации набор не меняется.
- **Жизненный цикл:** `advance_assembly(now)` (open → quiet после 5 с
  тишины, идемпотентно) вызывается фоновым `AssemblyTicker` общего сервера;
  `force_close(ref, reason)` — принудительное закрытие по лимиту с записью
  причины в `forced_reason` и quality-issue `forced_close` в графе.

Потребление памяти ограничено: читаются только спаны одной трассы.
