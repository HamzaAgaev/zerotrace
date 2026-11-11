# Handoff по A и интеграции

Документ описывает текущую структуру кода для команды. Согласованные решения первого показа — в [A_DECISIONS.md](architecture/A_DECISIONS.md), примеры оператора — в [API_A.md](architecture/API_A.md), фактические проверки и открытые пункты — в [VERIFICATION.md](../VERIFICATION.md).

## Состав

- Ветка `feat/a-stand-integrations` начинается от `0c69c01` (`0.3.0`) и включает выбранные части C. Общий entrypoint — `backend/app/server/application.py`; A собирает один Flask app и подключает OTLP, data API, product routes и analytics facade.
- A владеет registry, service adapters, plan/apply/readback/journal, стендом, faults, run lifecycle и idempotency: `backend/app/control/`, `backend/app/integrations/`, `backend/app/server/`.
- B сохраняет OTLP ingest, нормализацию, graph/data API и SQLite repository. Точный snapshot ревизий выбора run сохраняется в `frozen_trace_revisions`; это намеренно добавлено, чтобы поздние spans не сдвигали сравнение.
- C включён через `app/analytics_api/facade.py`: facade читает B telemetry store и A control snapshot, переводит зарегистрированные runtime configs в policy evidence и возвращает audit/comparison DTO. C не создаёт свои revisions и не владеет жизненным циклом run.
- D потребляет Product API через форму connections/services, plan dialog, journal, lab и сравнение. Product envelopes для array endpoints содержат `payload.items` массив; service adapter config остаётся объектом.

## Принятые границы

- A строит change plan, валидирует revision/fields, ведёт journal и выполняет PATCH/readback целиком. Сервис предоставляет `GET /_config`, `PATCH /_config`, `GET /_config/operations/{request_id}`.
- Внешняя конфигурация определяется server-side registry. Без зарегистрированного `control_url` сервис виден по телеметрии, но read/write config недоступны. Рекомендация применяется только к lab target.
- Run сохраняет карту конфигурационных ревизий всех сервисов, scenario/fault revisions, workload, seed, результаты и trace collection. B архивирует frozen refs.
- Готовый checkout уже поддерживает последовательные и параллельные группы. Генератор из трассы пока линейный; параллельное извлечение/генерация остаётся целевым развитием, не текущей возможностью.
- Docker Compose — текущий dev/first-show путь. Native Windows/macOS/Linux portability остаётся целью полной системы; наличие Docker реализации не подтверждает native совместимость.

## Проверенное состояние на 2026-11-11

Полный Python suite — 318 passed и 71 subtests, включая live/restart cases; backend Ruff, mypy contracts, Docker frontend tests/build и запуск 11 контейнеров demo прошли. Chromium подтвердил config/apply, сравнение 3→2 попытки, сохранение истории после пересоздания стенда и мобильный экран без JS-ошибок. Реальные сценарии и пределы проверки перечислены в [VERIFICATION.md](../VERIFICATION.md). Native OS support не проверялась; генератор из трассы поддерживает только линейную цепочку.

## Продолжение

Сверяйте контракт с `docs/architecture/A_DECISIONS.md` и API примерами с `docs/architecture/API_A.md`; используйте `VERIFICATION.md` для актуального статуса. Не переносите плановые критерии в текст как уже подтверждённые факты. Рабочую ветку не публиковать и не отправлять в remote без отдельного решения владельца.
