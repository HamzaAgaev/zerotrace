# Аналитика ZeroTrace

Python 3.11+, стандартная библиотека. В роли C реализованы правила и рекомендации P5, scorer P8, проекции P9, сравнение выборок P10 и лабораторных прогонов P11. HTTP, CLI и отчёты вызывают одни и те же функции.

Общий сервер использует `AnalyticsFacade` из `facade.py`, хранилище B и
снимки управления A. Маршруты общего сервера описаны в
[руководстве](../../../docs/guides/operations.md), границы модулей — в
[архитектуре](../../../docs/architecture/components.md). Общий сервер и отдельный сервер C используют
единые аналитические обработчики и [OpenAPI](openapi.json). Сохранённые выборки B архивирует в
`frozen_trace_revisions`, поэтому поздние spans не заменяют входы сравнения.

## Локальный запуск

Команды выполняются из `backend/` в контейнере разработки. Фикстура воспроизводит входы A и B для 30 синтетических запросов до и после смены лимита. Исходы и попытки записаны в спанах. Значения конфига и наблюдаемые попытки задаются независимо.

```sh
python fixtures/analytics/p11.py > analytics-demo.json
python -m app.analytics_api --data analytics-demo.json audit lab --mode trace_only
python -m app.analytics_api --data analytics-demo.json audit lab --mode config_aware > audit.json
python -m app.analytics_api eval audit.json fixtures/analytics/expected-r4.json
python -m app.analytics_api --data analytics-demo.json compare fixtures/analytics/compare-runs.json
python -m app.analytics_api --data analytics-demo.json compare fixtures/analytics/cohorts.json
python -m app.analytics_api --data analytics-demo.json project fixtures/analytics/project.json
python -m app.analytics_api --data analytics-demo.json export lab --format html --output analytics-report.html
python -m app.analytics_api --data analytics-demo.json serve --port 8001
python -m unittest discover -v
```

Команда `eval` с одним `audit.json` вернёт `blocked`: в ожиданиях указан `scenario_id`, а аудит такого идентификатора не содержит. Для оценки конкретного прогона передайте его фактические метаданные вместе с аудитом, как в [примере P8](../../evalkit/README.md).

Ожидаемый результат P11 — частоты 3 и 2 попытки на вход, 90 и 60 обращений, 30 ошибочных входов в каждой группе. Для независимого контрольного примера используйте `dataset(after_attempts=3)`: сохраните его как JSON и повторите команду сравнения. Анализ покажет три попытки после снижения лимита. Источник всех этих чисел — синтетические данные.

Для составного отчёта укажите один и тот же `--store results.sqlite3` перед командами `project`, `compare`, `state` и `export`. Команды `project` и `compare` возвращают `analysis_id`, а `state` возвращает `snapshot_id`. Передайте полученные ID в `export lab --projection-id … --comparison-id … --snapshot-id …`. Сохранённый отчёт читается по `GET /api/v1/reports/{report_id}` в сервере, запущенном с тем же хранилищем. Поле `input_refs.traces` перечисляет все ревизии из отчёта, включая исторические трассы обоих прогонов. В `input_refs.by_analysis` видна выборка каждого раздела. Формат `?format=html` автономен. Для отдельного запуска без `--store` используется SQLite в памяти.

Scorer запускается через `python -m app.analytics_api eval actual.json expected.json`. В [описании eval](../../evalkit/README.md) приведены входы и операнды. Коды CLI: 0 — расчёт выполнен, 1 — ошибка входа/исполнения или провал eval, 2 — достигнут заданный `audit --fail-on` уровень, 3 — недостаточно данных для обязательной проверки (`--require-rule`).

## Запуск с хранилищем телеметрии

Из `backend/` можно открыть существующую SQLite-базу `repository.SpanStore`. CLI читает базу через `--span-store`. Флаг `--store` по-прежнему указывает отдельную базу сохранённых результатов.

```sh
python -m app.analytics_api --span-store /path/to/spans.sqlite3 audit shop
python -m app.analytics_api --span-store /path/to/spans.sqlite3 serve --port 8001
```

В этом режиме на одном адресе доступны `GET /api/v1/traces`, `GET /api/v1/traces/{id}` и аналитические маршруты. Список и карточка содержат число находок по точной ревизии. Поле карточки `finding_occurrence_ids` позволяет найти все её записи в полном аудите, включая трассы за пределами пяти примеров в находке. Экран источников, журнал и операции изменения обслуживает компонент A.

Для локальной проверки можно заполнить новую базу двумя OTLP-примерами из репозитория. В примере приём, нормализация и запись выполняются кодом B.

```python
from pathlib import Path
from contracts import IngestLimits, SourceBinding
from ingest import RedactionProfile, SourceProfile, decode_otlp, normalize
from repository import SpanStore

binding = SourceBinding("demo", "demo-project", "shop")
with SpanStore(Path("spans.sqlite3")) as store:
    for i, name in enumerate(("s00_healthy", "s01_retry"), start=1):
        body = (Path("fixtures/otlp") / f"{name}.json").read_bytes()
        decoded = decode_otlp(body, "application/json", None, IngestLimits())
        profile = SourceProfile(source_id="demo", clock_domain="fixture-clock")
        store.ingest(normalize(decoded, profile, RedactionProfile()), binding, i)
```

Пример явно помечает часы сгенерированных спанов. Для прогноза ускорения и сравнения временных интервалов согласование временных отметок считается ответственностью пользователя источника: отсутствие или различие `clock_domain` само по себе не запрещает расчёт. Сервер не синхронизирует и не переписывает timestamps; проверки реальных границ и структуры сохраняются. Условия отдельных правил аудита учитываются отдельно.

## Подключение к A/B

```python
from app.analytics_api.routes import register_analytics
from app.analytics_api.service import AnalyticsService

analytics = AnalyticsService(source=repository_adapter, results=result_store)
register_analytics(flask_app, analytics)
```

`AnalyticsSource.read()` открывает согласованный снимок данных. Внутри него нужны три метода:

- `environment(id)` возвращает `model`, известные политики и опциональные `thresholds`, `baselines`, `journal_summary`, `capabilities`. Модель содержит `dataset_ref`, `environment_id`, доверенный `environment_kind`, `traces`, нормализованные `calls` и `attempts`. Для исторического анализа передавайте соответствующую ревизию конфигурации. Для исполнимой рекомендации адаптер передаёт в снимке явный `service_id` из реестра A. Одного имени сервиса достаточно только для отображения. Связи графа, качество и sampling передаются из B.
- `trace(ref)` возвращает ровно выбранную версию. Составной ключ включает окружение, trace ID, generation и revision. Модель примера хранит дополнительные ревизии и другие эпохи в `historical_traces`.
- `run(id)` возвращает состояние, стенд, scenario/fault revision, нагрузку, снимок конфига и зафиксированную `trace_collection`. Для общего случая у кандидата должен быть `expected_change` с владельцем, call_ref, полем и значениями до/после из подтверждённой операции A. Сценарий P11 допускает один однозначный вызов с изменением max_attempts.

Реализация A/B может инъектировать свой `ResultStore.put_many/get`. Локальная реализация хранит immutable-результаты атомарно и переживает перезапуск с файловой SQLite. Для P6 Никита вызывает `analytics.recommendation(environment_id, occurrence_id, recommendation_id)`, затем проверяет актуальные права, цель, capability и ревизию. Чистая функция `rules.verify_symptom` доступна планировщику A. Для встраивания в процесс создайте `AnalyticsService(RepositorySource(span_store, control_snapshot=read_control), result_store)`. `RepositorySource` и `BackendApp` находятся в `app.analytics_api.repository_source` и `app.analytics_api.backend_routes`. Функция `read_control()` возвращает входы A для одного чтения: `{"environments": {env_id: settings}, "runs": {run_id: run}}`. При отсутствии функции сервис работает по телеметрии B. Для P4 значения `policies` и `environment_kind` передаются в settings явно. Для P11 нужны метаданные двух прогонов A, их зафиксированные TraceRef, `run_id` входных SERVER-спанов и метки попыток CLIENT-спанов. Новая ревизия трассы даёт 409 для запроса прежней ревизии, если B уже заменил её по позднему спану. Ранее сохранённый отчёт остаётся доступным по ID.

## Публичный интерфейс

[OpenAPI](openapi.json) описывает маршруты роли C. Ответы в envelope P7. В JSON длительности в наносекундах передаются десятичными строками. Примеры ответов лежат в [fixtures/analytics](../../fixtures/analytics): `audit-budget.json`, `audit-healthy.json` и `audit-insufficient.json`. В полном `GET /state` доступны основания, выборки, пороги, данные о качестве и все срабатывания. Клиент запрашивает `GET /api/v1/audit?environment_id=…&view=full` и получает все три вида оснований: расчёт R4, наблюдения по трассам и наблюдения по политике (R8/D1). Для совместимости запрос без `view` возвращает прежние два вида оснований и перечисляет остальные в `omitted_findings`. Для R4 `budget_ns` хранит исходный бюджет, а `available_ns` — остаток после вычета прошедшего времени и резерва. Остаток может быть отрицательным. Флаг `recommendation.can_prepare_plan` показывает, есть ли у рекомендации исполнимый intent.

Сравнения прогонов и групп принимаются на одном маршруте по двум разным формам запроса. Для P10 алгоритм применяет P3 linear interpolation с округлением half-up в наносекундах. В P9 выбор вложенной ветки исключается с пояснением. Прогноз и сравнение групп в подключённом интерфейсе используют эти серверные API, без отдельной реализации вычислений в браузере. Пользовательский сценарий и допущения описаны в [руководстве по анализу трейсов](../../../docs/guides/trace-analysis.md).

Интеграционные тесты проходят путь от OTLP-фикстур через нормализацию B и SQLite до HTTP и CLI. Для проверки на запущенном лабораторном сервисе нужны стенд и управляющие метаданные A. Независимые измерения для P9 и размеченные сценарии для P8 предоставляет стенд A.
