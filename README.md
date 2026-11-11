# ZeroTrace + Retry Auditor

Хакатон MHS Fall 2026. Инструмент диагностики HTTP-трасс: находит опасные конфигурации retries/timeouts, объясняет расчёт и предлагает конкретное изменение.

## Структура

```
frontend/    React + TypeScript + Vite: интерфейс, API-клиент, граф/waterfall, аудит
backend/     Python: контракты (P1–P3), ingest/repository/core, data API, fixtures, тесты
docs/plans/  Спецификация, план команды и контракты ZeroTrace
```

## Актуальные документы

- `docs/architecture/A_DECISIONS.md` — согласованные изменения первого показа и границы решения; это не отчёт о проверках.
- `docs/architecture/API_A.md` — операторский запуск, подключение источника и примеры API.
- `docs/HANDOFF_Nikita.md` — устройство интеграционной ветки и handoff команде.
- `VERIFICATION.md` — результаты suite/build/runtime проверок и открытые проверки.
- `docs/plans/PLAN.md` — полная спецификация (v0.4.1-draft)
- `docs/plans/CONTRACTS.md` — контракты P1–P11
- `docs/plans/{A_stand,B_core,C_rules,D_product}.md` — зоны ответственности
- `frontend/FRONTEND_PLAN.md` — состояние фронтенда и задачи по команде
- `frontend/API.md` — текущие HTTP-маршруты и типы

## Разработка и первый показ

```sh
docker compose up --build
# UI и API: http://127.0.0.1:8000
docker compose --profile demo up --build
```

По умолчанию Compose запускает только ZeroTrace. Профиль `demo` включает девять приложений и `demo-load`; лабораторные стенды запускает API управления. Docker — основной путь разработки и первого показа. Нативная переносимость backend/CLI на Windows, macOS и Linux остаётся целевым требованием полной системы и здесь не заявляется как проверенная.

Подробности согласованных решений: [A_DECISIONS.md](docs/architecture/A_DECISIONS.md).

## Сборка образа

```sh
docker compose build
```

## Статус

Python (318 tests и 71 subtests), backend static checks, Docker frontend tests/build, Compose runtime и Chromium QA прошли согласно [VERIFICATION.md](VERIFICATION.md). Генератор из трассы пока линейный; готовый checkout поддерживает ветвления. Native portability остаётся непроверенным целевым требованием полной системы.
