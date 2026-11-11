# Контрактные примеры

Fixtures задают воспроизводимые входы и контрольные ответы для данных,
аналитики и интерфейса.

| Каталог | Содержимое |
|---|---|
| `model/` | Контрактные `TraceGraph`, `CallModel` и примеры run/TraceRef |
| `otlp/` | OTLP/JSON: успешные вызовы, retries, дубликаты и поздние spans |
| `product/` | Ответы data API, полученные сквозным прогоном конвейера |
| `analytics/` | Примеры аудита, входы сравнений/проекций и ожидаемые расчёты |

Генераторы данных запускаются из `backend/`:

```sh
python fixtures/build_model_fixtures.py
python fixtures/build_otlp_fixtures.py
python fixtures/build_product_fixtures.py
```

Сгенерированные `model/`, `otlp/` и `product/` не редактируются вручную.
В `analytics/` скрипт `p11.py` создаёт синтетический набор, а JSON-запросы
задают проверяемые сценарии; описание — в
[аналитическом модуле](../app/analytics_api/README.md).

Контрольные числа меняются вместе с причиной и проверками потребителей.
Тест совместимости data API читает
`frontend/fixtures/frontend-contracts.json` из корня репозитория.
