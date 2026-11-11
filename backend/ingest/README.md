# ingest

**Зачем:** входная граница системы — превращает сырые OTLP-пакеты в
нормализованные спаны, пригодные для хранения и анализа. Зона B (Миша).

**Как устроено:**

- `decoder.py` — `decode_otlp(body, media_type, encoding, limits)` принимает
  OTLP/HTTP protobuf и OTLP JSON, gzip с лимитом распаковки. Лимиты из
  `contracts.IngestLimits`. Ошибки типизированы под HTTP-коды протокола:
  400 (невалидные ID/времена), 413 (размер), 415 (media/encoding). Декодирует
  в промежуточные `RawSpan` с атрибутами «как есть».
- `normalize.py` — `normalize(batch, source_profile, redaction_profile)`:
  resource-атрибуты → service/namespace/instance/environment; kind/status
  из чисел OTLP в enum контракта; HTTP method/route/status, error.type;
  `duration_ns = end − start`. Redaction: query-строки и секретные
  атрибуты не сохраняются, идентификаторы в путях → placeholder с
  provenance-диагностикой. Извлекает lab-метки `zerotrace.run_id` и probe.
  На выходе `NormalizedBatch` (спаны + ресурсы + диагностика + run_ids).

**Граница:** здесь только чистые преобразования, никакого состояния и сети.
Следующий шаг — `repository` (запись) или `app/otlp_api` (HTTP-приём).
