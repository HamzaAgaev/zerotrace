# D — Frontend: интерфейс объединённой системы

**Ответственный: Агаев Хамза, MHS 1 курс, @blxssgod. Роль: Руководитель команды + Frontend.**


Версия 0.4.1-draft. Единственный фронтендер. Владеешь ui/ и ui/report-viewer/. Не пишешь Python backend, SQL/SQLite, OTLP decoder, control adapter, scheduler или серверный HTML/JSON builder.


## Первый показ с двумя режимами

Хамза ведёт интерфейс мониторинга и лаборатории. На одном графе показать все наблюдаемые сервисы и отдельно возможности выбранного сервиса: просмотр и ручное редактирование конфига. «Применить рекомендацию» показывать только для поддерживаемой цели на стенде. Для исходящего вызова указать, в каком сервисе хранится настройка. В лаборатории показать настройку сбоев, запуск и сравнение прогонов. Из мониторинга дать собрать стенд по выбранной трассе.

## Задачи Хамзы к первому показу

- [ ] Согласовать с командой общий сценарий показа и примеры API. Подключить готовый фронтенд сначала к fixtures, включая 403, 409 и состояния лаборатории. Затем подключить реальные API.
- [ ] Показать оба сервиса на одном графе. Для выбранного сервиса показать доступные действия с конфигом. Для вызова подписать, какой сервис владеет настройкой.
- [ ] Сделать просмотр плана и кнопку «Применить рекомендацию» для поддерживаемой цели на стенде. Показать подтверждённый результат, конфликт версии и неизвестный результат операции.
- [ ] Добавить «Собрать стенд» у выбранной трассы, показать восстановленные связи и значения по умолчанию. В лаборатории дать задать задержку или HTTP-ошибку, сбросить сбой и запустить запросы.
- [ ] Показать сравнение прогонов и ссылки на реальные трассы. Провести общий прогон команды и зафиксировать блокеры.
- [ ] Сверить реализацию и примеры с [рабочим срезом P11](CONTRACTS.md#p11-согласованный-рабочий-срез-для-первого-показа).

**Готовность.** Из мониторинга можно перейти от реальной трассы к стенду, запустить эксперимент и увидеть результат. Применение доступно только для поддерживаемой настройки.

## D1. Взаимодействие с backend

| Нужные данные/действия | К кому идти | Владелец сервера |
|---|---|---|
| Traces, graph, import/export, services/search/storage | B | Backend 2 |
| Audit/state, рекомендации/evidence, projection/cohorts, ReportBundle | C | Backend 3 |
| Sources/capabilities, config, concrete plans, apply, journal, lab capture | A | Backend 1 + Infra |

Работаешь по CONTRACTS, OpenAPI и mock fixtures с первого дня. Typed API client, единая обработка errors/stale/unknown, query state и polling/cancel. Общий backend один: фронтенд не знает внутреннее распределение серверных modules.

Не восстанавливаешь retries из spans и не рассчитываешь budget/gain/CV в JS. Презентационные units/formatting/filtering допустимы; аналитические числа и eligibility приходят с сервера. Frontend тестирует сериализацию/показ, не дублирует формулы.

## D2. Структура UI

Главные экраны: обзор/источники; traces; trace details; audit/политики/граф; план и журнал; what-if; сравнение; storage/settings. В первой демонстрации достаточно trace-view, finding, concrete plan и journal; вся остальная функциональность остаётся задачами следующих этапов.

UI браузерный, не зависит от Linux. Capabilities сообщают реальную доступность источников/управления на их ОС. Не скрывать весь продукт из-за отсутствующего eBPF; подключение OTLP/файла и поддержанных языковых агентов доступно отдельно.

Подключение без правки исходников объясняется честно: могут понадобиться пакеты и изменение запуска. UI не обещает универсального zero-setup на любом приложении/ОС.

## D3. Просмотр traces

Список: search/time/service/operation/duration/status/trace ID/profile; server-side pagination, сортировка и stable selection. Прямая ссылка/ref/revision, reload и expiry/unavailable. Не выдавать точную причину удаления без сохранённого основания.

Waterfall: исходные offsets/durations, parent tree, parallel branches, span status/target/quality. Поиск сохраняет родителей и ось; дерево не делится на бессвязные страницы. Большие таблицы/ranking виртуализируются/пагинируются. Client-only dependencies и unknown границы различимы.

Import JSON/JSONL/Tempo вызывает API B/A; export скачивает готовый ответ. Lab capture — отдельное явное действие через A, не часть пересчёта what-if.

## D4. Аудит

Граф на библиотеке: zoom/pan/search/fit, edge severity, idle, окрестность, only findings, DB/group filters. Политики: runtime/declared/inferred, source/confidence/age, timeout/attempts/backoff/jitter/retry_on, арифметика budget.

Finding: rule/version, severity/confidence, numerator/denominator, diagnosis hypothesis, operands/provenance, exemplar links, основной/альтернативный/временный вариант и риски. Not_implemented/insufficient_data отдельно от «нарушений не обнаружено».

Timing total/uncovered definitions не называются CPU. Calls-per-trace не становится retry-storm только из-за большого числа. Проценты/units корректно форматируются без потери точных JSON ns.

## D5. Plan/apply/journal

Получить у A concrete plan по occurrence/recommendation ref. Показать target, before→after, expected revision, risks, plan hash/expiry. По действию пользователя отправить apply, запретить случайный двойной click до результата.

Applied/no-op/rejected/failed/unknown имеют отдельные состояния. Unknown после timeout не даёт автоматический повтор мутации: UI опрашивает operation API. Stale plan → показать conflict и пересоздать план явным действием; не менять его незаметно.

Journal API A отображает фактические changes/config refs и verification отдельно. Config applied не равно root cause fixed. Checking/insufficient_data/symptom_absent/present/inconclusive различимы; errors/latency и sample coverage рядом. Нет трафика не выглядит успешным исправлением.

Без adapter — manual recommendation, apply capability=false. Frontend не генерирует arbitrary patch или control URL из trace. Multi-target partial показывается явно при появлении полной функции.

## D6. What-if

P9: root/subtree selector, один/до8 UI changes, проценты, исходные и projected values. Nested/excluded selections показываются с notice. Рейтинг25/50/100%, joint result и все limiting ties. Не суммировать gains строк.

Unavailable → reason codes/assumptions/null, а не0 gain. Поздняя trace revision приводит к явному rerun/new result, не скрытой смене входа. Наблюдаемая шкала остаётся исходной. Изменение процентов вызывает только analytics API C, никогда capture/apply.

## D7. Cohorts и отчёты

A/B group picker с фильтрами/pages и сохранёнными IDs; entry/workload/env/version и sampling видны. Small-sample/unknown/selected-only предупреждения рядом с median/p95/root/internal errors/direct calls/overlap/delta. Результат описательный, без причинного диагноза.

C поставляет ReportBundle и серверный JSON/HTML builder. D поставляет общий offline browser viewer, CSS и локальные JS assets. Работает без CDN/сети, без управляющих кнопок; текст span не исполняется как HTML. JSON и UI показывают одну семантику.

Storage UI B: TTL/logical+physical budget/evictions/losses; источник health A отдельно от отсутствия трафика. Native backend может работать на любой целевой ОС/удалённом хосте.

## Задачи

| ID | Этап | Что сделать | С чем интегрировать |
|---|---|---|---|
| D01 | M0, первые часы | Layout, роутинг, typed API client, моки/loading/errors | Fixtures A/B/C |
| D02 | M1, день1 | Trace list/tree + R4 finding/evidence | B data API + C audit/state |
| D03 | M1, день2 | Plan form, apply states, journal polling | A control/journal API |
| D04 | M1, день3 | Sample/unknown/stale UX, базовые фильтры, браузерные smoke | Все backend contracts |
| D05 | M2 | Полный audit graph/policies/timing/exemplars | C Snapshot и B details |
| D06 | M3 | Поиск/pages/direct links/storage и offline viewer/assets | B search + C report builder |
| D07 | M4 | Single/joint/subtree what-if, ranking/limiting branches | C projection API |
| D08 | M4 | Cohort picker/comparison и импорт/export UI | C comparison + A/B import |
| D09 | M5 | Большие datasets, доступность, browser/OS checks | CI A + реальные fixtures |

## Тесты и готовность

Компонентные fixtures и browser smoke: empty/loading/error, unknown/null, links/reload/expiry, pagination selection, malicious text/XSS, plan stale/idempotent UX, lost response/journal, no traffic, projection unavailable/nested/joint, cohorts mismatch/missing refs и offline viewer без сети.

Frontend должен собираться на Windows/macOS/Linux без обязательного bash. Проверяется browser поведение на доступной матрице, поддержка ОС backend не подменяется одним browser test. Готово, когда пользователь проходит общий сценарий без ручной правки данных, а вся серверная работа имеет владельцев A/B/C.
