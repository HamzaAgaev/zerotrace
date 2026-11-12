# Документация ZeroTrace

## Два основных раздела

- **[Для пользователей](users/README.md):** все сценарии по шагам со скриншотами интерфейса.
- **[Для разработчиков](developers/README.md):** запуск, настройка, архитектура, общие компоненты и проверки.
- [Дизайн-система](developers/design-system.md): типографика, поля, иконки, графики и мобильная версия.

## Устройство и контракты

- [Компоненты и структура кода](architecture/components.md).
- [Конфигурация сервисов и лаборатория](architecture/configuration-and-lab.md).
- [HTTP API интерфейса](../frontend/API.md).
- [API и исполнение тестового сервиса](../stand/README.md).

## Работа с системой

- [Запуск, подключение OTLP и управление](guides/operations.md).
- [Разработка и проверки](development/testing.md).
- [Интерфейс](../frontend/README.md).
- [Аналитический модуль и самостоятельный CLI](../backend/app/analytics_api/README.md).

## Планирование

[Общий план](plans/PLAN.md) и [контракты P1–P11](plans/CONTRACTS.md)
описывают целевую систему. Зоны команды: [A](plans/A_stand.md),
[B](plans/B_core.md), [C](plans/C_rules.md), [D](plans/D_product.md).
Плановые требования могут охватывать возможности за пределами текущего API.

- [JSON-сценарии: файл окружения и загрузка через интерфейс](guides/lab-scenario.md).
