# ZeroTrace + Retry Auditor

Хакатон MHS Fall 2026. Инструмент диагностики HTTP-трасс: находит опасные конфигурации retries/timeouts, объясняет расчёт и предлагает конкретное изменение.

## Структура

```
frontend/    React + TypeScript + Vite: интерфейс, API-клиент, граф/waterfall, аудит
docs/plans/  Спецификация, план команды и контракты ZeroTrace
```

## Актуальные документы

- `docs/plans/PLAN.md` — полная спецификация (v0.4.1-draft)
- `docs/plans/CONTRACTS.md` — контракты P1–P11
- `docs/plans/{A_stand,B_core,C_rules,D_product}.md` — зоны ответственности
- `frontend/FRONTEND_PLAN.md` — состояние фронтенда и задачи по команде
- `frontend/API.md` — текущие HTTP-маршруты и типы

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

## Статус

Мониторинг поддерживает демоданные и подключение к API. Лаборатория работает на тестовых данных и позволяет сравнить прогоны до и после применения рекомендации R4.
