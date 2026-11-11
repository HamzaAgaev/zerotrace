# Архитектурные решения ZeroTrace

Дата: 2026-11-11. Статус: решения первого показа и операторские материалы; актуальное состояние проверок — в [VERIFICATION.md](../../VERIFICATION.md). Chromium QA ещё выполняется, native portability остаётся непроверенной целью.

- [A_DECISIONS.md](A_DECISIONS.md) — Docker-first запуск, внешний demo профиль, конфигурационные адаптеры, checkout сценарий, faults, нагрузки и ревизии прогонов.
- [API_A.md](API_A.md) — операторская инструкция A: Docker, подключение OTLP source, product/service API и примеры faults/runs.
- [HANDOFF_Nikita.md](../HANDOFF_Nikita.md) — структура интеграционной ветки и границы владения A/B/C/D.
- [VERIFICATION.md](../../VERIFICATION.md) — проверенные результаты, ограничения и открытый Chromium QA.
- [README.md](../../README.md) — краткий запуск и границы заявлений о платформенной поддержке.
- [PLAN.md](../plans/PLAN.md), [A_stand.md](../plans/A_stand.md), [CONTRACTS.md](../plans/CONTRACTS.md), [BACKEND.md](../plans/BACKEND.md) — план и контракты с уточнениями первого показа.
