# Демо-чеклист первого показа

Сценарий P11 для чистого checkout. UI доступен на `http://127.0.0.1:8000`.

## Подготовка (за 10 минут до показа)

```sh
docker compose --profile demo up --build -d
docker compose ps                          # все 11 контейнеров healthy
curl -s http://127.0.0.1:8000/healthz      # {"status":"ok"}
```

Подождать ~1 минуту, пока demo-load накопит трассы (в списке Трейсов счётчик
запросов растёт; достаточно 20+).

## Шаг 1. Внешняя система (окружение shop)

**UI:** Трейсы — живой список; Сервисы → «Граф» — 9 узлов и связи с количеством вызовов.
**API (опционально):**

```sh
curl -s "http://127.0.0.1:8000/api/v1/audit?environment_id=shop&view=full" \
  | python3 -c "import json,sys; d=json.load(sys.stdin)['payload']; \
print([(f['rule_id'], f['edge']['caller'], f['edge']['callee']) for f in d['findings']])"
```

В `shop` граф строится из реальных трейсов. `orders` поддерживает ручное
изменение разрешённых полей, `payment` доступен для наблюдения. Применение
рекомендаций доступно в `sandbox`.

## Шаг 2. Сборка лаборатории (окружение sandbox)

**UI:** Лаборатория → «Создать стенд оформления заказа» (сценарий checkout, 9 сервисов, состояние «Готов»).
Или API: `POST /api/v1/lab/stands {"scenario_id":"checkout"}` → запомнить
`stand_id`, `config_revisions` (все `"1"`).

## Шаг 3. Сбой и baseline-прогон

**UI:** Лаборатория → форма «Сбой входящего вызова»: сервис `payment`,
входящий вызов `POST /charge`, вероятность ошибки `100`, код `503`,
дополнительная задержка `0`, точка «До исходящих вызовов» → «Сохранить сбой».

**UI:** Запросов 30, одновременных запросов 1, число для воспроизведения
запуска 42 → «Запустить прогон».

**API-прогон:** `POST /api/v1/lab/stands/{stand_id}/runs` с телом:

```json
{
  "request_id": "demo-baseline-001",
  "scenario_revision": 1,
  "expected_fault_revision": 1,
  "config_revisions": {"gateway":"1","orders":"1","inventory":"1","pricing":"1",
    "delivery":"1","payment":"1","fraud":"1","bank-api":"1","notifications":"1"},
  "request_count": 30, "concurrency": 1, "seed": 42
}
```

Ожидается: `status: completed`, `trace_collection.status: ready`,
`received_count: 30` (опрос `GET /api/v1/lab/runs/{run_id}`). Запомнить `run_id`.

## Шаг 4. Аудит и рекомендация

**UI:** Аудит (окружение sandbox) — проблема R4 `orders → payment`:
«Требуется 850 мс при доступном времени ожидания 700 мс», рекомендация
«Установить максимум 2 попыток, включая первую». Кнопка «Посмотреть план» в проблеме.

**API:** `GET /api/v1/audit?environment_id=sandbox&view=full` → finding с
`evidence.required_ns = "850000000"`, `budget_ns = "700000000"`,
`recommended_required_ns = "550000000"`,
`recommendation.can_prepare_plan = true`.

## Шаг 5. План → применение → журнал

Из finding: `POST /api/v1/change-plans` c `environment_id: sandbox`,
`occurrence_id`, `recommendation_id`, `expected_config_revision` из evidence →
план (`changes: max_attempts 3→2`); затем `POST /api/v1/change-plans/{plan_id}/apply`
с `plan_hash` и стабильным `request_id`.

Ожидается: операция `applied`, настройки orders → `max_attempts: 2`, версия конфигурации `2`.
**UI:** Журнал — статус «Настройки применены» и изменения «Было → Станет»;
настройки сервиса orders обновлены. Проверка проблемы показана отдельно.

## Шаг 6. Повторный прогон и сравнение

Второй run с **тем же seed 42** и `config_revisions` где `orders: "2"`,
новый `request_id`. Затем `POST /api/v1/comparisons`:

```json
{"baseline_run_id":"<run-1>", "candidate_run_id":"<run-2>"}
```

Ожидается по 30 из 30 трейсов. Число попыток на запрос снижается с 3 до 2.
В каждом прогоне `http_status_counts` содержит `503: 30`, а `change` указывает
на изменение `max_attempts` у `orders / charge-payment`.

**UI:** «Лаборатория» → «Сравнение прогонов» → выбрать прогоны до и после изменения.

## Шаг 7. Замыкание цикла

Повторный аудит sandbox: проблема R4 `orders → payment` больше не обнаруживается.
Проверка в журнале показывает, сохраняется ли исходная проблема.

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
