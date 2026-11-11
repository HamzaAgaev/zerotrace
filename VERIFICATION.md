# Проверка ZeroTrace

Дата: 2026-11-11, personal-laptop. Проверки выполнены из текущего workspace в Docker. Это отчёт о выполненных проверках; целевые native-платформы отдельно отмечены ниже.

## Проверки кода и сборки

- Полный Python suite: **318 passed, 71 subtests**. Включает 7 live integration cases и один restart case; они уже входят в это число.
- Backend Ruff прошёл. Mypy прошёл для contracts, 7 файлов.
- Docker frontend: **32 tests passed**, TypeScript и Vite production build прошли.
- `docker compose --profile demo` поднял 11 работающих контейнеров: ZeroTrace, девять demo-сервисов и `demo-load`. Все 10 настроенных healthchecks имеют статус healthy; у генератора нагрузки healthcheck не задан.

Команда полного Python suite:

```sh
docker build --target development -t zerotrace-python:local .
docker run --rm -v "$PWD":/app -w /app zerotrace-python:local \
  pytest -q backend/tests tests stand/tests
docker run --rm -v "$PWD":/app -w /app/backend zerotrace-python:local \
  sh -c 'ruff check . && mypy'
```

Ruff и mypy запускались в backend-контексте. Результат не означает, что весь проект Ruff-clean: у выбранных частей C остаются унаследованные style warnings; safety/import проверялись отдельно выбранным набором.

## Runtime-проверки

Проверены реальный OTLP от девяти сервисов, конфигурационный CAS и apply/readback, применение C intent, сравнение 3 попыток до и 2 после изменения, фиксация `frozen_trace_revisions`, ожидание дочерних spans после HTTP timeout, auto-instrumentation двух неизменённых Flask приложений и восстановление после restart общего сервера. Последний полный прогон: 318 passed, 71 subtests passed за 24.44 s.

Проверен также generator path: линейный сценарий из двух узлов прошёл живой прогон; topology с ветвлением была отклонена `422`. Готовый checkout preset при этом поддерживает параллельные группы.

## Chromium

Playwright прошёл полный сценарий против работающего Compose API/UI:

- Граф содержит девять сервисов и десять связей; конечные сервисы отображаются.
- Через интерфейс изменена и восстановлена политика внешнего сервиса.
- Создан checkout, изменена собственная задержка входящего маршрута, задан fault payment: вероятность 1, HTTP 503 до исходящих вызовов.
- При одинаковом seed `12345` выполнены два прогона по три запроса, concurrency 1. Gateway вызывает orders один раз; политика orders→payment изменена с 3 попыток на 2. Обе выборки `3/3 ready`; сравнение показывает **3 → 2 попытки на вход**. Ошибочных входов осталось три в каждой группе: изменение retry budget само по себе не устраняет fault.
- Fault очищен, изменённые конфигурации восстановлены. Стенд остановлен и создан снова; история прогонов сохранена.
- Проверен журнал изменений и мобильный viewport 390×844: горизонтального переполнения страницы нет.
- Ошибок JavaScript и неожиданных HTTP-ошибок нет. Ожидаемый `422 parallel_branches` при попытке собрать линейный стенд из ветвящейся трассы показан понятным сообщением.

Локальные артефакты проверки: `/home/n_logvi/wip/zerotrace-a-20261111/verify_ui.py`, `browser-result.json`, `graph-desktop.png`, `lab-comparison-desktop.png`, `journal-desktop.png`, `lab-mobile.png`. Промежуточные debug-артефакты в той же папке не являются результатом финального прогона.

## Границы

- Native compatibility для Windows, macOS и Linux не проверялась. Она остаётся целью полной системы, не подтверждённым результатом Docker-проверок.
- Генератор из трассы сейчас поддерживает только линейный путь; извлечение/генерация параллельных ветвей не реализованы в этом срезе.
