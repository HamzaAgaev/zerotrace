# Аналитика ZeroTrace

Python 3.11+, стандартная библиотека. В роли C реализованы правила и рекомендации P5, scorer P8, проекции P9, сравнение выборок P10 и лабораторных прогонов P11. HTTP, CLI и отчёты вызывают одни и те же функции. Основная программа A подключает модуль через `register_analytics`.

## Локальный запуск

Команды выполняются из корня репозитория. Фикстура воспроизводит входы A и B для 30 синтетических запросов до и после смены лимита. Исходы и попытки записаны в спанах. Значения конфига и наблюдаемые попытки задаются независимо.

```sh
python3.11 fixtures/analytics/p11.py > analytics-demo.json
python3.11 -m app.analytics_api --data analytics-demo.json audit lab --mode trace_only
python3.11 -m app.analytics_api --data analytics-demo.json audit lab --mode config_aware > audit.json
python3.11 -m app.analytics_api eval audit.json fixtures/analytics/expected-r4.json
python3.11 -m app.analytics_api --data analytics-demo.json compare fixtures/analytics/compare-runs.json
python3.11 -m app.analytics_api --data analytics-demo.json compare fixtures/analytics/cohorts.json
python3.11 -m app.analytics_api --data analytics-demo.json project fixtures/analytics/project.json
python3.11 -m app.analytics_api --data analytics-demo.json export lab --format html --output analytics-report.html
python3.11 -m app.analytics_api --data analytics-demo.json serve --port 8001
python3.11 -m unittest discover -v
```

Ожидаемый результат P11 — частоты 3 и 2 попытки на вход, 90 и 60 обращений, 30 ошибочных входов в каждой группе. Для независимого контрольного примера используйте `dataset(after_attempts=3)`: сохраните его как JSON и повторите команду сравнения. Анализ покажет три попытки после снижения лимита. Источник всех этих чисел — синтетические данные.

Для составного отчёта укажите один и тот же `--store results.sqlite3` перед командами `project`, `compare`, `state` и `export`. Команды `project` и `compare` возвращают `analysis_id`, а `state` возвращает `snapshot_id`. Передайте полученные ID в `export lab --projection-id … --comparison-id … --snapshot-id …`. Сохранённый отчёт читается по `GET /api/v1/reports/{report_id}` в сервере, запущенном с тем же хранилищем. Формат `?format=html` автономен. Для отдельного запуска без `--store` используется SQLite в памяти.

Scorer запускается через `python3.11 -m app.analytics_api eval actual.json expected.json`. В [описании eval](../../evalkit/README.md) приведены входы и операнды. Коды CLI: 0 — расчёт выполнен, 1 — ошибка входа/исполнения или провал eval, 2 — достигнут заданный `audit --fail-on` уровень, 3 — недостаточно данных для обязательной проверки (`--require-rule`).

## Подключение к A/B

```python
from app.analytics_api.routes import register_analytics
from app.analytics_api.service import AnalyticsService

analytics = AnalyticsService(source=repository_adapter, results=result_store)
register_analytics(flask_app, analytics)
```

`AnalyticsSource.read()` открывает согласованный снимок данных. Внутри него нужны три метода:

- `environment(id)` возвращает `model`, известные политики и опциональные `thresholds`, `baselines`, `journal_summary`, `capabilities`. Модель содержит `dataset_ref`, `environment_id`, доверенный `environment_kind`, `traces`, нормализованные `calls` и `attempts`. Для исторического анализа передавайте соответствующую ревизию конфигурации. Связи графа, качество и sampling передаются из B.
- `trace(ref)` возвращает ровно выбранную версию. Составной ключ включает окружение, trace ID, generation и revision. Модель примера хранит дополнительные ревизии и другие эпохи в `historical_traces`.
- `run(id)` возвращает состояние, стенд, scenario/fault revision, нагрузку, снимок конфига и зафиксированную `trace_collection`. Для общего случая у кандидата должен быть `expected_change` с владельцем, call_ref, полем и значениями до/после из подтверждённой операции A. Сценарий P11 допускает один однозначный вызов с изменением max_attempts.

Реализация A/B может инъектировать свой `ResultStore.put_many/get`. Локальная реализация хранит immutable-результаты атомарно и переживает перезапуск с файловой SQLite. Для P6 Никита вызывает `analytics.recommendation(environment_id, occurrence_id, recommendation_id)`, затем проверяет актуальные права, цель, capability и ревизию. Чистая функция `rules.verify_symptom` доступна планировщику A. Типы Python между компонентами и формат хранилища пока остаются внутренним интерфейсом.

## Публичный интерфейс

[OpenAPI](openapi.json) описывает маршруты роли C. Ответы в envelope P7. В JSON длительности в наносекундах передаются десятичными строками. Примеры для Хамзы лежат в [fixtures/analytics](../../fixtures/analytics): `audit-budget.json`, `audit-healthy.json` и `audit-insufficient.json`. В полном `GET /state` доступны основания, выборки, пороги, данные о качестве и все срабатывания. Текущий UI DTO аудита поддерживает R4 и наблюдения по трассам. У типов оснований, которые текущий валидатор фронтенда ещё не принимает, есть ссылки в `omitted_findings`; полные записи доступны в snapshot.

Сравнения прогонов и групп принимаются на одном маршруте по двум разным формам запроса. Для P10 алгоритм применяет P3 linear interpolation с округлением half-up в наносекундах. В P9 выбор вложенной ветки исключается с пояснением. Эти две семантики закреплены в командном черновике и расходятся с текущим локальным превью TypeScript.

Реальный интеграционный прогон требует поставщиков A/B с OTLP, их хранилищем и лабораторией. Готовый WSGI-модуль и `register_analytics` дают точки подключения. Независимые измерения для валидации точности P9 и размеченные сценарии для P8 предоставляет стенд A.
