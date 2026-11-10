# API frontend-среза для A/B/C

Это конкретные **view DTO первого frontend**, не утверждение о готовности Python-сервера и не совместимость со старыми Go/Rust handlers. Основа — P7 проектных контрактов. Формы ниже относятся к публичному API фронтенда. Полный Snapshot и внутренние модели аналитики описаны отдельно.

Полные типы: `src/api/types.ts`. Реальные по форме демопримеры: `src/api/fixtures.ts` и `fixtures/frontend-contracts.json`.

## Целевой срез с двумя режимами

Целевой API добавляет возможности чтения и записи по сервису и полям, применение рекомендации только на стенде и отдельные лабораторные операции. Ниже зафиксирован интерфейс текущего прототипа. Целевые операции описаны в [P6–P7](../docs/plans/CONTRACTS.md#p6-план-и-применение).

Лаборатория в этой рабочей копии доступна в демонстрационном режиме. Типизированный fixture в `src/api/labFixture.ts` поддерживает сборку по точной ревизии трассы orders → payment, фиксацию выборок и применение R4 к конфигу стенда. Лабораторные операции выполняются в памяти вкладки. Возможности внешних сервисов на графе тоже подписаны как fixtures. В режиме API клиент использует существующие маршруты мониторинга и предпросмотра плана из таблицы ниже.

После подключения серверных операций на первом показе фронт показывает один активный стенд. Кнопка «Остановить» вызывает DELETE /api/v1/lab/stands/{id} и считает стенд остановленным после подтверждения сервера. Во время прогона кнопка и редактирование заблокированы; если запрос всё же дошёл, сервер возвращает 409 run_in_progress. При 409 stale_revision фронт обновляет конфиг или сценарий и ждёт подтверждения пользователя. Форматы и правила описаны в [P11](../docs/plans/CONTRACTS.md#p11-согласованный-рабочий-срез-для-первого-показа).

При открытии лаборатории и после конфликта фронт читает GET /api/v1/lab/stands/active. Для одного нажатия «Запустить» используется один request_id, включая повтор после сетевого таймаута. В состоянии stopping доступны просмотр и повтор остановки. Сравнение ждёт фиксации трасс обоих прогонов. Для partial показываются полученное и ожидаемое число трасс.

Сборка ограничена шестью секундами на сервере. Если браузер не получил ответ на создание, он перечитывает активный стенд и сравнивает полный source TraceRef с выбранной трассой. Стенд со статусом building ещё нельзя использовать. Повторное создание выполняется по явному действию пользователя после проверки состояния.

## Общие правила

Все success responses — JSON envelope:

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
| GET /api/v1/traces?environment_id=&limit=100&offset=0 | B | `{items:TraceSummary[],total:number}` |
| GET /api/v1/traces/{id}?environment_id=&generation=&revision= | B | `TraceDetail` |
| GET /api/v1/audit?environment_id= | C | `AuditResult` |
| GET /api/v1/capabilities?environment_id= | A | `Capabilities` |
| GET /api/v1/journal?environment_id= | A | `JournalEntry[]` |
| POST /api/v1/change-plans | A | `ChangePlan` |

JournalEntry использует requested_at и actual_changes, статусы из P6 и verification из P7 либо null до проверки. Коллекция journal необходима экрану истории; endpoint `/journal/{id}` можно добавить позже для polling операций. Список первого среза загружает первые100 и фильтрует/пагинирует локально. Если total>100, интерфейс сообщает, что загружена часть. Полный server search/cursor — следующий этап.

### Для Михаила (B)

TraceSummary: trace_ref, trace_id, operation, service, start_ns, duration_ns, span_count, service_count, outcome(ok/error/unknown), http_status:number|null, finding_count, quality_issues:string[].

TraceDetail: `{summary,trace_ref:{environment_id,trace_id,generation,revision},spans,quality_issues}`. Span содержит span_id,parent_span_id|null,service,name,kind(server/client/internal),start_offset_ns,duration_ns,outcome,http_status|null,error_type|null. `start_offset_ns` считается для выбранной шкалы на сервере/adapter; frontend не вычитает timestamps разных clocks. Готовность модели и clock quality остаются у B/C.

### Для Амира (C)

AuditResult: result_id,generated_at,findings,rule_evaluations. Finding содержит готовые evidence operands/required/budget; браузер только форматирует ns и показывает текст. Конкретные refs occurrence/recommendation нужны для запроса plan. Для R4 evidence.kind = retry_budget. Для наблюдений без конфига evidence = {kind: trace_observation, source}; карточка отображает объяснение и рекомендацию без расчёта бюджета. exemplars содержит полные TraceRef.

Пример evidence: attempts3,timeout_ns1000000000,backoff_total_ns150000000,required_ns3150000000,budget_ns2000000000,config_revision"12",source"Конфигурация стенда". Полные ключи/структура в types.ts.

Статусы rule_evaluations: evaluated/not_implemented/insufficient_data/not_applicable, eligible_count. Недоступный audit возвращает error; пустая findings не используется как замена недоступного алгоритма.

### Для Никиты (A)

Capabilities: mode,api_version,source_name,source_state(receiving/idle/unknown),apply_enabled,limits{max_trace_spans}. Даже если сервер сообщает apply_enabled=true, frontend этого среза остаётся readonly и не вызывает apply.

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

Frontend вызывает только создание плана и отображает результат. В этом срезе не существует POST apply; нет имитации applied/journal и автоматического повтора мутации после timeout.

При разных origins разрешить CORS для локального frontend, включая Content-Type POST. Auth/production URL/политика доступа согласуются отдельно. Никита не должен принимать произвольные target URLs из этих DTO как адреса управления.

## Расширение frontend 0.2

Новые экраны используют существующие list/details/audit. Дополнительных HTTP endpoints этот frontend не вызывает.

- Граф/тайминги: максимум 100 details с concurrency4; traces с более чем 2000 спанов пропускаются с предупреждением. Граф выводит до 40 сервисов. Один cross-service server span и его client-parent считаются одним наблюдаемым вызовом. Повторы не объединяются в логический запрос; это не коэффициент retry amplification. Неизвестный исход хранится отдельно.
- Медиана/p95 — nearest-rank. Для связи берётся client-parent interval, при его отсутствии server interval; показатели объединённой выборки не имеют одного гарантированного источника. При переходе к серверным агрегатам нужен явный duration_basis для каждой метрики.
- Время вне дочерних интервалов — server duration минус clipped union непосредственных children, суммируется по операциям; не CPU и не глобальный wall time.
- Редактор видит только config_aware findings. Полного списка политик нет. Live backoff не восстанавливается из суммы пауз: пользователь задаёт fixed backoff явно. Демочерновик начинает с 75 мс средней паузы для суммы 150 мс; рекомендация с двумя попытками использует 50 мс.
- Экспорт черновика: `zerotrace.exploration` 1.0.0, payload kind=policy-draft, mode, applied=false, occurrence_id, target, expected_config_revision, assumptions, draft{timeout,attempts,backoff,budget}, check. Размерности draft в мс. Это локальный документ, не POST payload и не ChangePlan.
- Открытие исходного плана рекомендации по-прежнему вызывает POST /api/v1/change-plans с точными refs. Этот план не включает ручные правки; интерфейс это явно объясняет.
- What-if: локальная browser-interval-blocks-preview-v1, до 8 непересекающихся выбранных веток. Корень один, родители известны, bounds строгие, cycles/depth>100/quality issues запрещены. Ускорение ветки масштабирует её длительность целиком, внутри родителя объединяются перекрывающиеся блоки, сохраняются offsets запусков; последовательные блоки сжимаются. Авторитетный расчёт позже должен прийти от C. Экспорт содержит trace_ref, branches, result, measured=false, applied=false.
- Группы: только одна пара service/operation, непересекающиеся ids из загруженной выборки; медиана, p95, errors, unknown. Изменения структуры и причинность не вычисляются.

Для следующего этапа A/B/C должны согласовать отдельные versioned DTO для полного реестра политик, серверного графа/таймингов, what-if и cohorts. Не следует объявлять локальные exports готовыми wire contracts этих endpoints.
