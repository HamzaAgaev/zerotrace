# API frontend-среза для A/B/C

Это публичные DTO frontend для объединённого Python-сервера A/B/C. Все успешные ответы обёрнуты envelope v1; полные frontend типы находятся в `src/api/types.ts`.

Полные типы: `src/api/types.ts`. Реальные по форме демопримеры: `src/api/fixtures.ts` и `fixtures/frontend-contracts.json`.

## Целевой срез с двумя режимами

Серверный режим использует тот же origin по умолчанию и позволяет читать конфигурацию, строить и применять планы, просматривать журнал и выполнять лабораторные прогоны. Деморежим сохраняет локальные fixtures. Рекомендации разрешено применять только в `sandbox`.

GET services/environments возвращает `{items:[]}`; GET services/{id}/config возвращает конфиг сервиса с `calls` и входящими `routes`. POST change-plans принимает окружение, сервис, ревизию, call_ref или route и typed `set`. Применение передаёт `request_id` и `plan_hash`; одинаковый request_id сохраняется при повторе. Результат доступен через журнал операций.

Лаборатория читает активный стенд, историю прогонов и обновляет состояние выполняющейся нагрузки. Fault редактируется с `expected_fault_revision`; прогон передаёт scenario/fault/config revisions, request_count, concurrency и необязательный числовой seed. При stale_revision интерфейс перечитывает состояние. Остановка считается завершённой после подтверждения API.

История прогонов загружается через GET /api/v1/lab/runs. Сравнение выполняется POST /api/v1/comparisons с baseline_run_id и candidate_run_id после фиксации трасс; метрики показываются из фактических результатов C. Частичная выборка отображает received/expected.

Сервер отводит на сборку checkout 20 секунд. Если браузер не получил ответ на создание, он перечитывает активный стенд и сравнивает полный source TraceRef с выбранной трассой. Стенд со статусом building ещё нельзя использовать. Повторное создание выполняется по явному действию пользователя после проверки состояния.

## Общие правила

Клиент запрашивает JSON envelope.

```json
{
  "schema": "zerotrace.trace-list",
  "schema_version": "1.0.0",
  "generated_at": "2026-11-10T11:24:00Z",
  "producer_version": "0.1.0",
  "payload": {"items": [], "total": 0}
}
```

Frontend проверяет major schema1 и обязательные nested fields перед render. `*_ns` — десятичные строки; отсутствующий status/null не превращается в HTTP0. Отмена/смена connection отменяет устаревшие requests; timeout8сек. Поддерживаются только HTTP(S) base URLs без credentials/query/hash.

Ошибки:

```json
{"error":{"code":"stale_revision","message":"Конфигурация изменилась","retryable":false}}
```

HTTP409 не становится demo fallback. На CORS/network/schema error показывается явное сообщение. Разделы audit/journal имеют независимые ошибки: отсутствие journal endpoint не блокирует data API.

## Endpoints

| Метод/путь | Владелец | payload |
|---|---|---|
| GET /api/v1/traces?environment_id=&method=&span_id=&start_from_ns=&start_to_ns=&limit=100&offset=0 | B | `{items:TraceSummary[],total:number}` |
| GET /api/v1/traces/{id}?environment_id=&generation=&revision= | B | `TraceDetail` |
| GET /api/v1/audit?environment_id=&view=full | C | `AuditResult` |
| GET /api/v1/capabilities?environment_id= | A | `Capabilities` |
| GET /api/v1/journal?environment_id= | A | `JournalEntry[]` |
| POST /api/v1/change-plans | A | `ChangePlan` |

Обычный запрос имеет лимит 5 секунд. В списке «Сначала новые» без фильтров сначала запрашивается страница, затем отдельно загружаются доля успешных запросов и варианты фильтров. Запрос списка с расчётом окон успеха и `include_findings=false` имеет лимит 10 секунд. Первое чтение списка, карточки и выборки графа использует `include_findings=false`. Телеметрия доступна до получения аудита. Список и карточка догружают данные аудита отдельно и применяют их по точному TraceRef. В ответе без аудита `finding_count: 0` — значение для совместимости схемы. На его основании не показывается отсутствие проблем.

Аудит запрашивается с `defer=true`. На `202` фронтенд читает `job_id` и опрашивает тот же маршрут примерно раз в 750 мс до получения `200` с AuditResult. Лимит ожидания 5 секунд применяется к отдельному запросу, весь цикл может занять до 45 секунд. Переход отменяет опрос по AbortSignal.

JournalEntry использует requested_at и actual_changes, статусы из P6 и verification из P7 либо null до проверки. Коллекция journal необходима экрану истории; клиент отслеживает операции через `GET /api/v1/journal/{id}`. Главный список передаёт в API `query`, `service`, `status`, `method`, `span_id`, `start_from_ns`, `start_to_ns`, `sort` (recent или slow), `limit` и `offset`. API фильтрует и сортирует до пагинации. Запрос с сортировкой или фильтрами также возвращает `services`, `methods` и счётчики `outcomes` по найденной выборке. Граф и сравнение групп используют выборку до 100 трасс.

### Для Михаила (B)

Фильтр `method` сопоставляет HTTP-метод входного запроса без учёта регистра и применяется вместе с текстовым поиском. Список показывает метод и эндпоинт в отдельных колонках. `span_id` ищет точный Span ID в текущей ревизии, а `start_from_ns` и `start_to_ns` ограничивают время начала Trace полуоткрытым интервалом в наносекундах UTC.

TraceSummary: trace_ref, trace_id, operation, http_method (строка или null), endpoint, service, start_ns, duration_ns, span_count, service_count, outcome(ok/error/unknown), http_status:number|null, finding_count, quality_issues:string[].

TraceDetail: `{summary,trace_ref:{environment_id,trace_id,generation,revision},spans,quality_issues,finding_occurrence_ids}`. Карточка выбирает находки по полному списку `finding_occurrence_ids` выбранной ревизии. Span содержит span_id,parent_span_id|null,service,name,start_offset_ns,duration_ns,outcome,http_status|null,error_type|null. По умолчанию ответ включает `kind` (server/client/internal) для анализа зависимостей. Карточка запрашивает `include_span_kind=false` и исключает `kind` из JSON-отчёта. `start_offset_ns` считается для выбранной шкалы на сервере/adapter; frontend не вычитает timestamps разных clocks. Готовность модели и clock quality остаются у B/C.

У клиентского спана может быть строковое поле `target` с наблюдаемым адресатом вызова, например `payment` из `server.address`. Если серверный спан не найден, граф показывает отдельный узел адресата. Время ответа сервиса рассчитывается по его серверным спанам.

### Для Амира (C)

AuditResult: result_id,generated_at,findings,rule_evaluations. Finding содержит готовые evidence operands/required/budget; браузер только форматирует ns и показывает текст. Конкретные refs occurrence/recommendation нужны для запроса plan. Для R4 evidence.kind = retry_budget. Для наблюдений без конфига evidence = {kind: trace_observation, source}; карточка отображает объяснение и рекомендацию без расчёта бюджета. Полный аудит также возвращает `policy_observation` для выводов по конфигурации. `exemplars` содержит до пяти примеров с полной TraceRef.

Пример evidence: attempts3,timeout_ns1000000000,backoff_total_ns150000000,required_ns3150000000,budget_ns2000000000,config_revision"12",source"Конфигурация стенда". Полные ключи/структура в types.ts.

Статусы rule_evaluations: evaluated/not_implemented/insufficient_data/not_applicable, eligible_count. Недоступный audit возвращает error; пустая findings не используется как замена недоступного алгоритма.

### Для Никиты (A)

Capabilities: mode,api_version,source_name,source_state(receiving/idle/unknown),apply_enabled,limits{max_trace_spans}.

Запрос создания плана:

```json
{
  "environment_id":"shop",
  "occurrence_id":"occ-demo-001",
  "recommendation_id":"rec-demo-001",
  "finding_id":"r4-orders-payment",
  "expected_config_revision":"12"
}
```

ChangePlan: plan_id,plan_hash,finding_id,target,expected_revision,expires_at,changes[{field,before,after}],warnings:string[],apply_enabled. Здесь changes — **человекочитаемый DTO**: field/before/after строки, например «Таймаут одной попытки»/«1 000 мс»/«800 мс». Исполняемый typed patch остаётся на сервере и не генерируется браузером из этих строк.

После явного подтверждения frontend вызывает POST `/api/v1/change-plans/{plan_id}/apply` с request_id и plan_hash. Если ответ потерян, пользователь может повторить применение с тем же request_id. Статусы и actual_changes читаются из журнала.

Лабораторные endpoints: GET/POST `/api/v1/lab/stands/active` и `/api/v1/lab/stands`; PUT/DELETE `/api/v1/lab/stands/{id}/fault`; POST `/api/v1/lab/stands/{id}/runs`; GET `/api/v1/lab/runs/{id}` и `/api/v1/lab/runs`; POST `/api/v1/comparisons`.

При разных origins разрешить CORS для локального frontend, включая Content-Type POST. Auth/production URL/политика доступа согласуются отдельно. Никита не должен принимать произвольные target URLs из этих DTO как адреса управления.

## Расширение frontend 0.2

Граф и what-if остаются браузерными расчётами по загруженным трассам; редактор конфигурации, применение и лабораторные операции используют endpoints A. Сравнение показывает расчёт C по сохранённым выборкам.

- Граф/тайминги: максимум 100 details с concurrency4; traces с более чем 2000 спанов пропускаются с предупреждением. Граф выводит до 40 сервисов. Один cross-service server span и его client-parent считаются одним наблюдаемым вызовом. Повторы не объединяются в логический запрос; это не коэффициент retry amplification. Неизвестный исход хранится отдельно.
- Медиана/p95 используют линейную интерполяцию по отсортированным значениям, как в P3. Для связи берётся client-parent interval, при его отсутствии server interval; показатели объединённой выборки не имеют одного гарантированного источника. При переходе к серверным агрегатам нужен явный duration_basis для каждой метрики.
- Время вне дочерних интервалов — server duration минус clipped union непосредственных children, суммируется по операциям; не CPU и не глобальный wall time.
- Редактор видит только config_aware findings. Серверные политики доступны через `GET /api/v1/policies`. Live backoff не восстанавливается из суммы пауз: пользователь задаёт fixed backoff явно. Демочерновик начинает с 75 мс средней паузы для суммы 150 мс; рекомендация с двумя попытками использует 50 мс.
- Экспорт черновика: `zerotrace.exploration` 1.0.0, payload kind=policy-draft, mode, applied=false, occurrence_id, target, expected_config_revision, assumptions, draft{timeout,attempts,backoff,budget}, check. Размерности draft в мс. Это локальный документ, не POST payload и не ChangePlan.
- Открытие исходного плана рекомендации по-прежнему вызывает POST /api/v1/change-plans с точными refs. Этот план не включает ручные правки; интерфейс это явно объясняет.
- What-if: локальная browser-interval-blocks-preview-v1, до 8 непересекающихся выбранных веток. Корень один, родители известны, bounds строгие, cycles/depth>100/quality issues запрещены. Ускорение ветки масштабирует её длительность целиком, внутри родителя объединяются перекрывающиеся блоки, сохраняются offsets запусков; последовательные блоки сжимаются. Серверный расчёт C доступен через `POST /api/v1/projections`. Экспорт содержит trace_ref, branches, result, measured=false, applied=false.
- Группы: только одна пара service/operation, непересекающиеся ids из загруженной выборки; медиана, p95, errors, unknown. Изменения структуры и причинность не вычисляются.

Для серверного сравнения групп передайте две выборки TraceRef в `POST /api/v1/comparisons`. Форматы проекции, сравнения и отчётов описаны в [OpenAPI аналитики](../backend/app/analytics_api/openapi.json).
