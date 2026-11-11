# contracts

**Зачем:** общие типы данных всех участников проекта. Это «договор» между
Мишей (B, данные), Никитой (A, стенд/инфра), Амиром (C, аналитика) и Хамзой
(D, фронтенд): каждый пишет код против этих структур, не дожидаясь чужих
модулей. Контракты P1–P3 из `docs/plans/.../CONTRACTS.md`.

**Что внутри:**

| Файл | Содержание |
|---|---|
| `base.py` | Идентичность: `ServiceKey`, `OperationKey`, `TraceRef` (окружение + trace ID + generation + revision), `EndpointKey`, `EdgeKey` со стабильным `edge_id` (SHA-256 канонического JSON). |
| `p1.py` | Приём телеметрии: `IngestLimits`, `SourceCapabilities`, `SourceBinding`. |
| `p2.py` | Модель данных: `NormalizedSpan`, `TraceGraph`, `Quality`, `Attempt`, `LogicalCall`, `CallModel`. |
| `p3.py` | Рёбра и статистика: `Sourced<T>` (значение + источник), `EdgePolicy`, `EdgeStats`, контрактный перцентиль. |
| `p7.py` | View-DTO для data API фронтенда: `TraceSummary`, `SpanView`, `TraceDetail`, `Envelope`. |
| `validate.py` | Валидатор по правилам протокола (hex ID, end ≥ start и т.д.). |

**Ключевые инварианты:** время — int64 наносекунды, в JSON `*_ns` —
десятичными строками; unknown — `None` (не 0/false); CLIENT и SERVER
статусы не смешиваются. Пакет не имеет зависимостей и не меняется без
согласования с потребителями.
