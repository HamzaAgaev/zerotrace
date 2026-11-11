# A — Backend 1 + Infra: подключения, инфраструктура и управление

**Ответственный: Никита Логвиненко, MHS 1 курс, @n_logvi. Роль: Backend + Infra/Integrations.**


Версия 0.4.1-draft. Один из трёх бэкендеров; дополнительно единственный ответственный за инфраструктуру. Полная роль до завершения объединённой системы, не только первого показа. Код — после согласования архитектуры.


## Первый показ

Согласованные изменения объёма и API описаны в [A_DECISIONS.md](../architecture/A_DECISIONS.md). Этот документ содержит целевые требования полной системы; ниже отмеченные решения первого показа уточняют, но не отменяют их.

Никита ведёт стенд, API управления и адаптеры для внешних сервисов. Модули данных и аналитики сохраняют свои API. По каждому сервису сервер проверяет доступные операции и поля. Первый лабораторный checkout запускается по готовому `scenario_id`; генерация из выбранной цепочки трассы — следующий этап. Для стенда A реализует внутренние ручки, faults и повторяемые прогоны с реальным OTLP и меткой прогона.

## Задачи Никиты к первому показу

- [ ] Поднять общий Python-сервер с роутерами B/C. Внешнюю демонстрационную систему из девяти приложений запускать отдельно через Compose-профиль `demo`; ZeroTrace по умолчанию запускается без неё.
- [ ] Настроить реальный экспорт OTLP и передать Мише пример трассы с исходящим вызовом между сервисами.
- [ ] Отдать Хамзе API возможностей сервиса, конфигурации и операций. A владеет планированием и применением целиком; адаптер сервиса предоставляет `GET /_config`, `PATCH /_config` и `GET /_config/operations/{request_id}`. Не вводить отдельный plan/apply API на самом стенде.
- [ ] Обеспечить checkout стенд из девяти сервисов по `POST /api/v1/lab/stands` с `scenario_id`. Генератор сценария из трассы подключить позже; линейная генерация допустима первой, параллельная опциональна. Вести состояние прогонов и передавать Мише срок фиксации выборки. Показать наблюдаемые параметры и значения по умолчанию. Направлять запросы только на лабораторные сервисы.
- [ ] Добавить route-scoped fault: probability, HTTP status, дополнительную задержку, fixed/random execution boundary и сброс. Запускать одинаковый сценарий повторно и передавать run_id в телеметрии.
- [ ] Сверить реализацию и примеры с [рабочим срезом P11](CONTRACTS.md#p11-согласованный-рабочий-срез-для-первого-показа).

**Готовность.** Из одного графа можно изменить поддерживаемую настройку первого сервиса. По выбранной трассе поднимается стенд, на котором воспроизводятся сбой и повторный прогон.

## Ответственность

Владеешь deploy/, stand/, scenarios/, app/server/, app/integrations/, app/control/. Запуск одного backend, подключение routers B/C, языковые источники/Collector optional, внешние adapters, конфиги, план/apply/reconcile, journal и verification scheduler.

Не пишешь за B ingest/storage/граф, за C — аналитические формулы, за D — UI. Используешь repository-интерфейс B для операций и journal, не создаёшь второй независимый JSON-store. Чистую оценку симптома предоставляет C.

## A1. Кроссплатформенный запуск и CI

Целевые ОС backend/CLI: Windows, macOS, Linux. Native-путь без Docker, root, kernel modules и Linux VM обязателен. Контейнерный и eBPF-профили — optional.

Поставляешь переносимые Python entrypoints для server/stand/load, настройку env/ports/paths, pyproject/lock и инструкции. Bash/Make могут быть shortcuts, но не единственным способом. Не использовать обязательные Unix signals/fork/systemd/Unix sockets. Graceful HTTP/process stop и flush работают на доступных native ОС. Windows spawn и пути с пробелами проверяются отдельно.

CI: backend/CLI tests на всех трёх ОС, UI build/browser checks совместно с D, schema/format checks, миграции/temp files. Каждый владелец пишет свои tests, A собирает pipelines. Матрица фиксирует actual tested, target, unsupported: обещание поддержки не равно фактической проверке.

Для полной системы остаётся требование native smoke и приёмки на Windows/macOS/Linux; это целевая переносимость, не подтверждение проверки. В разработке и первом показе Docker Compose — основной путь, зависимости первого старта должны запускаться в контейнерах. Согласованная граница записана в [A_DECISIONS.md](../architecture/A_DECISIONS.md).

## A2. Источники наблюдения

Три независимых пути:

1. Уже настроенный OTLP или импорт файла — без установки агента самим продуктом.
2. Языковая auto-instrumentation/agent: пакеты/launcher/env/JVM flag, без изменения исходного кода приложения.
3. OBI/eBPF как дополнительный Linux-источник с явными kernel/permission requirements.

Ни один агент не нужен для старта backend/UI. Источник может работать на другой ОС/машине. Первый кандидат — Flask+requests с opentelemetry-instrument; поддержанные версии и ОС проверяются, не предполагаются. Затем отдельные профили Java, Node.js/.NET по реальной потребности. Общий backend не зависит от языка наблюдаемой системы.

SourceCapabilities по P1: runtime/library/OS/connection mode/fields/sampling/losses, installed packages и необходимость restart. Не выдавать runtime monkey-patching за отсутствие установки/вмешательства вообще. Обязательное требование — без редактирования исходников, а не универсальное подключение без настроек.

Первые 2–4 ч вместе с B: общая trace_id, parent/client/server связи под конкуренцией, operation/target/status/duration и реальные error/retry признаки. A отвечает за launcher/agent, B — за корректность decoding/graph. Если поля недоступны — ограничить конкретные правила, не весь продукт.

Tempo/source HTTP adapter принадлежит A: URL только server-configured, лимиты/timeouts, ожидание поздних spans. Данные идут в единый ingest B. Source health не выдаёт отсутствие трафика за подтверждённый отказ агента.

## A3. Стенд и нагрузка

Независимые сервисы без импорта аналитики. Первый срез gateway→order→payment, затем inventory/pricing/fraud/black-box/db ветки. По процессу на сервис, portable supervisor. В сценариях version/seed/workload/config epochs.

Поддержать work fixed либо lognormal с задаваемыми p50/p99, serial/parallel groups, общий timeout попытки, retries/backoff/jitter/retry_on, repeat/hedge, breaker, ignore/fail. Fault на входящем route задаёт вероятность, HTTP status, дополнительную задержку и execution boundary (fixed или random среди отмеченных точек). Cancellation/deadline/idem только с реальной поддержкой; добавленный заголовок не обещает серверную дедупликацию.

Open-loop load не ждёт предыдущего ответа: inflight cap, dropped/started/completed, warmup/drain и реальные trace IDs. Client/server errors отдельно. Управляющие ручки не создают бизнес-spans. SDK-профиль лаборатории помечен отдельно от автоматической инструментации.

## A4. Серверные конфиги и применение

Владеешь P4/P6, журналом и API лаборатории; построение/подтверждение/применение плана целиком принадлежит A. UI D только отображает план и отправляет действие.

- Trusted adapter registry, capabilities и config read/freshness/revision.
- Преобразование рекомендации C в конкретный plan с target, field before/after, expected revision, hash/expiry и рисками.
- Pending journal до мутации; CAS и idempotency key/request ID; atomic single-service apply.
- Timeout после отправки → unknown, operation lookup/readback → reconcile. Не слепой повтор.
- Конфликт/unsupported/invalid field → явный отказ; no-op виден.
- Startup recovery и блок следующей мутации target до выяснения старой.
- Config applied отдельно от symptom verification; scheduler получает eligibility/statistics C и refs B.

Первый adapter — service-owned конфигурация с операциями GET/PATCH и проверкой результата по request_id; см. [A_DECISIONS.md](../architecture/A_DECISIONS.md). Для чужих приложений подключать поддержанный config/proxy adapter; без него ручной план. Полный multi-target orchestration позже: per-target results/partial, отдельный CAS rollback plan, без ложной distributed atomicity. До/после хранятся, archive не очищается.

## A5. Независимые сценарии и measured validation

A пишет expected, C scorer: healthy/budget, storm, backoff/jitter, idem risk, forbidden status, wasted/cancel, loop/hedge/breaker/blackbox/clock skew/wrapper/no annotation, late duplicate/config conflict. Trace-only не получает hidden fault config; config-aware явно получает P4.

Для what-if: serial/parallel/nested/shifted/ties/joint/gaps/invalid. Сохранить reportlab baseline/price-fast/both-fast/serial/chatty: реальные ускорения, одинаковый содержательный ответ, минимум5 повторов после прогрева. Predictor не читает hidden delays. Данные/trace IDs/среда/ошибка прогноза публикуются; обычный what-if не запускает приложение.

## Задачи по этапам

| ID | Этап | Конкретный результат | Проверка |
|---|---|---|---|
| A01 | M0, первые часы | Native launcher, auto-source profile, общий router skeleton | Старт без Linux/Docker и связанная трасса с B |
| A02 | M1, день1 | Стенд s00/s01, ConfigSnapshot, config API stub | Реальные fixtures и config-aware R4 |
| A03 | M1, день2 | A-owned planning/apply/journal/readback и service adapter API | Stale/idempotent/lost-response сценарии |
| A04 | M1, день3 | CI skeleton и native smoke второго OS-профиля | Зависимости/версии/ограничения записаны |
| A05 | M2 | Полные retry-сценарии и independent expected | Поведение сначала подтверждено стендом |
| A06 | M3 | Control recovery, optional Compose, native CI3OS | Journal restart, partial/failure, graceful stop |
| A07 | M4 | Tempo adapter и независимая projection/cohort lab | Late merge, no-replay counter, measured runs |
| A08 | M5 | Матрица источников/ОС, нагрузка/overhead | Проверенные пределы, не универсальные обещания |

## Что передать коллегам

B: source profiles и реальные OTLP. C: сценарии/expected без скрытых ответов в анализаторе. D: OpenAPI/examples для config/plan/apply/journal/source health, ошибки/loading/polling states. C получает реальные operation/config epochs для verification.

Готово: коллега запускает native-продукт без Linux-зависимости, получает данные, применяет supported plan через UI и видит подтверждённый результат/ограничения; все сценарии воспроизводимы.
