# Разработка и проверки

Команды выполняются из корня репозитория. Зависимости устанавливаются
в контейнеры; запуск не требует Python/OpenTelemetry пакетов на хосте.

## Python

```sh
docker build --target development -t zerotrace-dev .
docker run --rm -v "$PWD":/app -w /app zerotrace-dev \
  pytest -q backend/tests stand/tests
```

Для отдельной области замените аргумент pytest на:

- `backend/tests/control` — валидация, CAS, идемпотентность, readback и восстановление.
- `backend/tests/analytics` — правила, рекомендации, проекции и сравнение.
- `backend/tests/integration` — реальный HTTP/OTLP, запуск процессов и перезапуск сервера.
- `stand/tests` — сценарии, снимки конфигурации, ошибки и воспроизводимость.

Интеграционные тесты используют временные каталоги и свободные локальные порты
внутри контейнера. Они не обращаются к пользовательским приложениям.

Проверки контрактов и серверных модулей управления/данных:

```sh
docker run --rm -v "$PWD":/app -w /app/backend zerotrace-dev \
  ruff check contracts core ingest repository app/control app/integrations app/server app/data_api app/otlp_api
docker run --rm -v "$PWD":/app -w /app/backend zerotrace-dev mypy
```

Mypy настроен на `contracts`. У аналитических модулей собственный объём
проверок; приведённая команда Ruff не охватывает все пакеты проекта.

## Интерфейс

Frontend stage запускает тесты, проверку TypeScript и production build:

```sh
docker build --target frontend -t zerotrace-ui-check .
```

Для разработки UI с локальным Node.js команды и API описаны в
[frontend/README.md](../../frontend/README.md).

## Проверка общей системы

```sh
docker compose --profile demo up --build -d
docker compose ps
```

В интерфейсе проверьте граф внешней системы, изменение разрешённого поля
через план и журнал, создание checkout, fault и два прогона с одинаковой
нагрузкой/seed. Для примера уменьшите `orders → payment POST /charge`
с трёх попыток до двух при фиксированном HTTP 503: сравнение должно показать
меньше попыток, а количество ошибочных входов останется прежним.

Для остановки с сохранением данных:

```sh
docker compose --profile demo down
```
