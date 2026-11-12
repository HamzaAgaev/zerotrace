# Документация ZeroTrace

## Знакомство с проектом

- [Возможности и ограничения](guides/capabilities.md).
- [Прогноз ускорения и сравнение групп трейсов](guides/trace-analysis.md).
- [Сборка и первый запуск](guides/getting-started.md).
- [Сценарий демонстрации](guides/demo-checklist.md).
- [Интерактивная архитектура](architecture/overview.html) и [SVG для экспорта](architecture/overview.svg).

## Устройство и действующие контракты

- [Компоненты и структура кода](architecture/components.md).
- [Конфигурация сервисов и лаборатория](architecture/configuration-and-lab.md).
- [Подключение OTLP и API управления](guides/operations.md).
- [HTTP API интерфейса](../frontend/API.md).
- [API и исполнение тестового сервиса](../stand/README.md).
- [Экраны и состояние интерфейса](../frontend/README.md).
- [Аналитический модуль и самостоятельный CLI](../backend/app/analytics_api/README.md).

## Разработка

- [Проверки и тесты](development/testing.md).
- [Типизированная модель аналитики](development/analytics-core-model-migration.md).
- [Контракты и примеры данных](../backend/fixtures/README.md).

## Требования и дальнейшее развитие

[Общий план](plans/PLAN.md), [контракты P1–P11](plans/CONTRACTS.md) и
[требования к интерфейсу](plans/FRONTEND_UNIFICATION.md) сохраняют проектные
решения и критерии приёмки. Полный план шире поставляемой версии: описание
действующих функций и границ находится в руководствах выше.
