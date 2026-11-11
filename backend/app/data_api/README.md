# app/data_api

**Зачем:** product API данных для фронтенда (P7): список трасс и детали
трасс в view-форме, которую рисует UI Хамзы (D). Зона B (Миша).

**Как устроено:**

- `handlers.py` — чистые функции-обработчики без фреймворка (роутер
  подключает Никита, A): `list_traces(store, environment_id, limit,
  offset)` → Envelope `zerotrace.trace-list` (сортировка
  first_received DESC, limit ≤ 100); `get_trace(store, graph_builder,
  environment_id, trace_id, generation, revision)` → Envelope
  `zerotrace.trace`. Ошибки типизированы `ApiError`: 400 на неполную
  пару версий, 404 на недоступный ref. Хранилище описано Protocol
  `TraceStore` — реализация в `adapter.py`.
- `views.py` — конверсия `TraceGraph`/`CallModel` → `TraceSummary` /
  `SpanView` / `TraceDetail` (contracts.p7): outcome из корневого SERVER
  спана; `start_offset_ns` считается здесь (фронт часы не вычитает);
  CLIENT HTTP-статус в вывод не попадает (статусы не смешиваются).
- `adapter.py` — мост `repository.SpanStore` → `TraceStore` и фабрика
  `make_graph_builder(store, limits)`, закрывающая сигнатуру
  `core.graph.build_graph`.

См. также `../otlp_api` (приём телеметрии) — оба пакета живут под `app/`,
который собирает A в один процесс.
