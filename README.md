# ZeroTrace + Retry Auditor

Хакатон MHS Fall 2026. Инструмент диагностики HTTP-трасс: находит опасные конфигурации retries/timeouts, объясняет расчёт и предлагает конкретное изменение.

## Структура

```
frontend/    React + TypeScript + Vite: интерфейс, API-клиент, граф/waterfall, аудит
backend/     Python: контракты (P1–P3), ingest/repository/core, data API, fixtures, тесты
docs/plans/  Спецификация, план команды и контракты ZeroTrace
```

## Актуальные документы

- `docs/plans/PLAN.md` — полная спецификация (v0.4.1-draft)
- `docs/plans/CONTRACTS.md` — контракты P1–P11
- `docs/plans/{A_stand,B_core,C_rules,D_product}.md` — зоны ответственности
- `frontend/FRONTEND_PLAN.md` — состояние фронтенда и задачи по команде
- `frontend/API.md` — текущие HTTP-маршруты и типы

## Серверная аналитика

[Запуск аналитики, CLI и подключение к общему бэкенду](app/analytics_api/README.md).

## Запуск фронтенда

```sh
cd frontend
npm ci
npm run dev      # http://127.0.0.1:5173
```

Проверки:

```sh
npm run build    # строгая TypeScript-проверка + production build
npm test         # автотесты
```

## Проверки бэкенда

```sh
cd backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check contracts fixtures tests
.venv/bin/ruff format --check contracts fixtures tests
.venv/bin/mypy
.venv/bin/pytest tests -q
python3 fixtures/build_model_fixtures.py   # пересобрать контрактные fixtures
```

## Статус

Мониторинг поддерживает демоданные и подключение к API. Лаборатория работает на тестовых данных и позволяет сравнить прогоны до и после применения рекомендации R4.
