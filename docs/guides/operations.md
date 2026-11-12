# Запуск, подключение и управление

По умолчанию Product API возвращает JSON envelope вида `schema`, `schema_version`, `generated_at`, `producer_version`, `payload`. Списки находятся в `payload.items`; конфигурация, план, стенд и прогон — объектами в `payload`. API телеметрии `/v1/traces` отвечает стандартным OTLP ответом.

## Запуск

```sh
docker compose up --build
docker compose --profile demo up --build
```

Первый запуск включает ZeroTrace, UI и API на `http://127.0.0.1:8000`. Профиль `demo` дополнительно запускает gateway, orders, inventory, pricing, delivery, payment, fraud, bank-api, notifications и `demo-load`. Compose хранит базу и состояние в именованных volumes. Обычный `docker compose down` их сохраняет.

## Подключение собственного источника

Добавьте запись `{"source_id":"my-api","environment_id":"shop"}` в `sources` файла `deploy/config.json`. При старте сервер создаёт для неё случайный токен в token volume по пути `shop/my-api`; файл имеет права `0600`. Значение не выдаётся через Product API и не пишется приложением в логи. Если нужен read/write config, отдельно добавьте service с `service_id`, `environment_id`, server-side `control_url` и ограниченным `write_fields`. Для одного наблюдения запись service не нужна.

Подключите собственное приложение к тому же Compose project/network и смонтируйте token volume только для чтения. Например, дополнение Compose сервиса:

```yaml
services:
  my-api:
    volumes:
      - source-tokens:/tokens:ro
    environment:
      OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: http://zerotrace:8000/v1/traces
      OTEL_EXPORTER_OTLP_PROTOCOL: http/protobuf
      OTEL_TRACES_SAMPLER: always_on
```

На старте приложения небольшой локальный wrapper читает `/tokens/shop/my-api` и задаёт экспортёру заголовок `Authorization: Bearer <token>` через `OTEL_EXPORTER_OTLP_TRACES_HEADERS` (в формате OTLP env var: `Authorization=Bearer%20<token>`). Не вписывайте token в compose YAML, исходники или команды, которые печатают окружение. Если вы изменили registry, пересоздайте ZeroTrace; затем перезапустите приложение с автоинструментацией. Контейнер приложения должен отправлять OTLP protobuf на внутренний адрес `zerotrace:8000`, не на опубликованный host port.

Для приложения, запущенного на том же компьютере вне Docker, используйте endpoint `http://127.0.0.1:8000/v1/traces` и передайте ему тот же token из защищённого источника, доступного этому процессу. Сервер принимает только Bearer token, зарегистрированный для source; он сам назначает `environment_id`. Compose сейчас публикует UI/API только на loopback.

## Product API

Операционные маршруты работают с `environment_id` (по умолчанию `shop`). Примеры ниже показывают тело запроса; успешный ответ содержит результат внутри `payload`.

| Задача | Маршрут |
|---|---|
| Список окружений | `GET /api/v1/environments` → `payload.items` — массив |
| Сервисы и возможности | `GET /api/v1/services?environment_id=shop` → `payload.items` — массив |
| Конфиг сервиса | `GET /api/v1/services/orders/config?environment_id=shop` → один config object |
| План изменения | `POST /api/v1/change-plans` → один plan object, `201` |
| Применение | `POST /api/v1/change-plans/{plan_id}/apply` → operation object, `202` |
| Журнал | `GET /api/v1/journal?environment_id=shop` → массив операций; `GET /api/v1/journal/{operation_id}?environment_id=shop` → одна операция |
| Активный стенд | `GET /api/v1/lab/stands/active` → объект или `payload: null` |
| Создать стенд | `POST /api/v1/lab/scenarios/validate` с `{"scenario": {…}}`, затем `POST /api/v1/lab/stands` с `{"scenario_hash":"полученный-hash", "request_id":"уникальный-id"}`; для привязанного файла используйте `{"environment_id":"shop"}`; формат в [lab-scenario.md](lab-scenario.md). Генерация по source/TraceRef отключена. |
| Fault | `PUT /api/v1/lab/stands/{stand_id}/fault`; сброс — `DELETE` по тому же адресу |
| Run | `POST /api/v1/lab/stands/{stand_id}/runs` |
| Ошибки и повторы сервисов теста | `GET /api/v1/lab/runs/{run_id}/services` → analyzed_traces, missing_traces, services с error_traces/retry_traces (точные TraceRef) |
| Run history | `GET /api/v1/lab/runs` → `payload.items` — массив; `GET /api/v1/lab/runs/{run_id}` → один объект |
| Сравнение | `POST /api/v1/comparisons` принимает пару `baseline_run_id` и `candidate_run_id` либо пару списков `baseline_trace_refs` и `candidate_trace_refs` |

Полный аудит окружения доступен через `GET /api/v1/audit?environment_id=shop&view=full`.
Карточка трассы возвращает связанные с её ревизией `finding_occurrence_ids`.
`GET /api/v1/state` сохраняет снимок аналитики, а `POST /api/v1/projections` вычисляет
проекцию для точной ревизии трассы. В `GET /api/v1/report` можно передать
`snapshot_id`, `projection_id` и `comparison_id`. Готовый отчёт доступен по
`GET /api/v1/reports/{report_id}` в JSON или с `?format=html`.
Формы запросов приведены в [OpenAPI аналитики](../../backend/app/analytics_api/openapi.json).

### Конфигурация и применение

План целиком принадлежит A. Запрос выбирает одно service и один target (`call_ref` для исходящего вызова или `route` для входящего маршрута), сверяет текущую revision и показывает before/after:

```json
{
  "environment_id": "sandbox",
  "service_id": "orders",
  "route": "POST /orders",
  "expected_config_revision": "3",
  "set": {"work": {"kind": "lognormal", "p50_ms": 10, "p99_ms": 30}}
}
```

Ответ плана содержит `plan_id`, `plan_hash`, `expires_at` (10 минут), target и changes. Подтверждение передаёт hash и стабильный `request_id`:

```json
{"request_id":"apply-orders-001","plan_hash":"<hash-from-plan>"}
```

Повтор того же request ID с тем же plan/hash возвращает ту же операцию; повтор с другим содержимым — `409 request_id_conflict`. Устаревшая config revision — `409 stale_revision`. Запись рекомендации разрешена только в `sandbox`. Потерянный или не подтверждённый ответ получает состояние `unknown`, после чего A сверяет результат по service API; не отправляйте новую операцию вслепую.

За записью A обращается к сервисному adapter API: `GET /_config`, `PATCH /_config`, `GET /_config/operations/{request_id}`. PATCH включает `request_id`, `expected_revision`, ровно один selector (`call_ref` или `route`) и map `set`. Адаптер проверяет compare-and-swap и сохраняет повторяемый результат операции. Это API сервиса, а не отдельный plan/apply API стенда. Fault идет отдельными service control endpoints `PUT /_chaos` и `DELETE /_chaos`.

Исходящая retry policy хранится у вызывающего сервиса и выбирается по `call_ref`, независимо от истории входящего запроса. Для входящего маршрута отдельно настраивается собственная работа `work`: `{"kind":"fixed","delay_ms":5}` либо `{"kind":"lognormal","p50_ms":10,"p99_ms":30}`. Вызовы downstream идут дополнительно к этой работе. Конфиг фиксируется при входе HTTP-запроса, поэтому новые revisions применяются к следующим запросам.

### Сценарий, faults и runs

Готовый checkout использует scenario `checkout`. Сборка по внешней трассе поддерживается отдельно через `{"source":{"environment_id":"shop","trace_id":"…","generation":1,"revision":2}}`; текущий генератор извлекает линейную цепочку и назначает лаборатории локальные адреса.

Fault задаётся для входящего route сервиса:

```json
{
  "service_id": "payment",
  "route": "POST /charge",
  "error_rate": 1.0,
  "http_status": 503,
  "delay_ms": 0,
  "request_id": "fault-payment-001",
  "expected_fault_revision": 0,
  "failure_point": {"mode": "fixed", "boundary_id": "before-first"}
}
```

Для случайной границы укажите `{"mode":"random","boundary_ids":["before-first","after-fraud"]}`; выбор равновероятен среди разрешённых boundary IDs. `error_rate` задаёт долю запросов, на которых включится fault. Fault и delay запускаются после нормальной работы этого маршрута до выбранной границы.

Создайте run с актуальными revisions из ответа стенда (`scenario_revision`, `fault_revision`, `config_revisions`):

```json
{
  "request_id": "checkout-run-001",
  "scenario_revision": 1,
  "expected_fault_revision": 1,
  "config_revisions": {
    "gateway": "1", "orders": "1", "inventory": "1",
    "pricing": "1", "delivery": "1", "payment": "1",
    "fraud": "1", "bank-api": "1", "notifications": "1"
  },
  "request_count": 30,
  "concurrency": 4,
  "seed": 42
}
```

В `config_revisions` передавайте полную карту сервисов из активного стенда; значения revision приходят от сервисных adapters и в текущем демо — строки. `request_count` допустим от 1 до 1000, `concurrency` от 1 до 32. `seed` необязателен; если опущен, A создаёт и сохраняет его. Seed относится только к воспроизводимой нагрузке лаборатории и не требуется от OTLP источников/пользовательских приложений. Повтор request ID с тем же телом возвращает тот же run; изменённое тело даёт `409 request_id_conflict`. После выполнения телеметрия может иметь `waiting`, `ready` или `partial`; сравнение использует зафиксированные ссылки трасс.

## Хранение и границы API

Одна SQLite база `/data/zerotrace.db` используется B и A. Основные таблицы B: `meta`, `traces`, `spans`, `trace_runs`, `run_samples`, `frozen_trace_revisions`. Последняя сохраняет точные metadata/spans ревизии, выбранные в зафиксированной sample, чтобы поздний ingest не менял сравнение. A хранит plans, operations, runs, stand, fault/run request idempotency records в `control_records(kind,key,payload)` той же базе. C использует facade над B telemetry store и read-only snapshot A control records; свои артефакты записывает в те же control records через namespaced kinds. Сериализация записи проходит транзакционно; база работает в WAL.

Product API обычно отдаёт object/array в envelope `payload`; списки сервисов и run history находятся в массиве `payload.items`, а список операций журнала — сам массив `payload`. Это не map по service ID. Сервисный `GET /_config`, напротив, отдаёт объект: `calls` — массив, `routes` — объект, ключи которого являются route strings. Реестр сервера отдельно задаёт адрес управления и разрешённые `write_fields`.
