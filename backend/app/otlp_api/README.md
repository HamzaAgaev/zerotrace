# app/otlp_api

**Зачем:** ручка приёма телеметрии `POST /v1/traces` по P1 — единая
входная точка для внешней системы и лабораторного стенда. Зона B (Миша);
HTTP-роутер и токен-реестр источников предоставляет Никита (A).

**Как устроено:**

- `handler.py` — `handle_traces(store, token_registry, body, headers,
  limits, received_at_ns)` → `OtlpResponse{status, body, media_type}`.
  Поток: `Authorization: Bearer <token>` → `SourceBinding` через
  `TokenRegistry` Protocol (неизвестный/отсутствующий токен → 401) →
  `ingest.decoder.decode_otlp` (маппинг 400/413/415 из исключений) →
  `ingest.normalize` с профилем источника → `repository.SpanStore.ingest`.
  **Успех (200) — только после commit транзакции; сбой записи → 503,
  ложного ACK нет.** Ответ — настоящий `ExportTraceServiceResponse`
  (protobuf или JSON — в формате запроса). Дубликат пакета принимается
  как успех без двойного счёта (гарантию даёт store).

Правило изоляции окружений: окружение определяется серверной привязкой
источника (токен → environment_id), а не полями внутри пакета — внешние
и лабораторные трассы разделены даже при совпадении имён сервисов.
