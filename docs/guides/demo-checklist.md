# Демо-чеклист первого показа

Сценарий проверен сквозным прогоном 2026-11-11 на чистом стенде (записано Мишей,
зона B). Время полного прохода — ~5 минут. UI доступен на `http://127.0.0.1:8000`.

## Подготовка (за 10 минут до показа)

```sh
docker compose --profile demo down -v      # чистая база и токены
docker compose --profile demo up --build -d
docker compose ps                          # все 11 контейнеров healthy
curl -s http://127.0.0.1:8000/healthz      # {"status":"ok"}
```

Подождать ~1 минуту, пока demo-load накопит трассы (в списке Трейсов счётчик
запросов растёт; достаточно 20+).

## Шаг 1. Внешняя система (окружение shop)

**UI:** Трейсы — живой список; Граф сервисов — 9 узлов, связи с количеством вызовов.
**API (опционально):**

```sh
curl -s "http://127.0.0.1:8000/api/v1/audit?environment_id=shop&view=full" \
  | python3 -c "import json,sys; d=json.load(sys.stdin)['payload']; \
print([(f['rule_id'], f['edge']['caller'], f['edge']['callee']) for f in d['findings']])"
```

Ожидается: находки R4 на рёбрах `payment -> bank-api` (внешний callee виден
по исходящему спану). Рекомендации есть, `can_prepare_plan: false` — внешнее
окружение только на чтение.

## Шаг 2. Сборка лаборатории (окружение sandbox)

**UI:** Лаборатория → «Создать стенд» (scenario checkout, 9 сервисов, статус ready).
Или API: `POST /api/v1/lab/stands {"scenario_id":"checkout"}` → запомнить
`stand_id`, `config_revisions` (все `"1"`).

## Шаг 3. Сбой и baseline-прогон

**UI:** Лаборатория → форма «Сбой входящего маршрута»: сервис `payment`,
маршрут `POST /charge`, вероятность `1`, код `503`, задержка `0`,
точка «До исходящих вызовов» → «Применить сбой».

**API-прогон:** `POST /api/v1/lab/stands/{stand_id}/runs` с телом:

```json
{
  "request_id": "demo-baseline-001",
  "scenario_revision": 1,
  "expected_fault_revision": 1,
  "config_revisions": {"gateway":"1","orders":"1","inventory":"1","pricing":"1",
    "delivery":"1","payment":"1","fraud":"1","bank-api":"1","notifications":"1"},
  "request_count": 8, "concurrency": 4, "seed": 42
}
```

Ожидается: `status: completed`, `trace_collection.status: ready`,
`received_count: 8` (опрос `GET /api/v1/lab/runs/{run_id}`). Запомнить `run_id`.

## Шаг 4. Аудит и рекомендация

**UI:** Аудит (переключить окружение на «Лаборатория») — находка R4
`orders -> payment`: «Требуется 2,5 с > Бюджет 1,8 с», рекомендация
«Установить максимум 2 попыток». Кнопка плана из рекомендации.

**API:** `GET /api/v1/audit?environment_id=sandbox&view=full` → finding с
`evidence.required_ns = "2500000000"`, `budget_ns = "1800000000"`,
`recommendation.can_prepare_plan = true`.

## Шаг 5. План → применение → журнал

Из finding: `POST /api/v1/change-plans` c `environment_id: sandbox`,
`occurrence_id`, `recommendation_id`, `expected_config_revision` из evidence →
план (`changes: max_attempts 3→2`); затем `POST /api/v1/change-plans/{plan_id}/apply`
с `plan_hash` и стабильным `request_id`.

Ожидается: операция `applied`, конфиг orders → `max_attempts: 2`, revision `2`.
**UI:** Журнал — операция с before/after; конфиг сервиса orders обновлён.

## Шаг 6. Повторный прогон и сравнение

Второй run с **тем же seed 42** и `config_revisions` где `orders: "2"`,
новый `request_id`. Затем `POST /api/v1/comparisons`:

```json
{"baseline_run_id":"<run-1>", "candidate_run_id":"<run-2>"}
```

Ожидается: `attempts_per_input` уменьшилось (было ~6, станет ~4),
`http_status_counts` неизменны (`503: 8`), `change` опознан автоматически
(`max_attempts 3→2, charge-payment, orders`).

**UI:** «Сравнение групп» — дельта попыток на вход.

## Шаг 7. Замыкание цикла

Повторный аудит sandbox: находка R4 `orders -> payment` **исчезла**.

## Резервные номера (если спрашивают)

- **Перезапуск:** `docker compose restart zerotrace` — история, журнал и
  конфигурации сохраняются (проверено тестом `test_restart_live`).
- **Изоляция окружений:** одинаковые trace ID из shop и sandbox не смешиваются
  (`test_source_binding_isolates_identical_trace_ids`).
- **Приём OTLP:** 401 на неизвестный Bearer-токен, 413 на большой пакет,
  200 только после записи в SQLite.

## Запреты на показе

- Не мерджить ветки и не пересобирать образ после репетиции.
- Не чистить volumes до конца показа (`down` без `-v`).
- Не применять планы к окружению shop (403 `recommendation_lab_only`).
