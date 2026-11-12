# Контракты между компонентами

Комплект **0.4.1-draft**. Целевая первая версия протоколов — **1.0.0 после согласования**. Здесь спецификация полной объединённой системы, а не готовая реализация моделей. P1–P8 сохраняют привычные названия из исходных файлов, P9/P10 добавляют функции ZeroTrace. Изменённые типы не совместимы автоматически с прототипами. U-идентификаторы из расширенного исследования здесь не используются. Первый профиль и полный целевой профиль обозначены отдельно: отсутствие функции в первом показе не исключает её из задания.

## 0. Общие соглашения

- Внутри `int64` наносекунды. Абсолютное время — Unix epoch; длительности ≥0. В JSON поля `*_ns` — десятичные строки для сохранения точности в браузере.
- Trace ID — 32 lowercase hex, span ID — 16, ненулевые. Нет родителя — `null`.
- `Ratio` — число [0,1]. UI показывает проценты; знаменатель всегда известен или значение `null`.
- Unknown — `null` с причиной; не заменяется нулём/false/успехом.
- Корневой внутренний артефакт: `schema`, `schema_version`, `generated_at` RFC3339 UTC, `producer_version`, `payload`. OTLP сохраняет стандартную структуру без нашей обёртки.
- Версии после 1.0: optional-аддитивное поле — minor; удаление/переименование/смена смысла/единиц — major; исправление без изменения контракта — patch. Новые enum-варианты требуют согласованного unknown fallback либо major.
- Изменение схемы сопровождается примерами и ревью потребителей. `contracts/schemas/` в будущей реализации задаёт структуру; этот документ задаёт семантику. CI проверяет их соответствие.

Базовые типы:

```text
ServiceKey: {project_id, environment, namespace, service_name}
OperationKey: {protocol, method?, route_or_name, source, normalization_version}
TraceRef: {environment_id, trace_id, generation, revision}
EdgeKey: {caller: ServiceKey, caller_operation: OperationKey,
          callee: EndpointKey, callee_operation: OperationKey}
EndpointKey: service(ServiceKey) | external(address_alias) | db(system) | unknown
```

Project берётся из доверенной настройки источника. Display name отдельно от ServiceKey; переименование не меняет историю. Неизвестное environment не приравнивается к production. `service.instance.id` задаёт отдельное наблюдаемое измерение; один service.name не доказывает общие часы.

Машинный `edge_id` — SHA-256 канонического JSON EdgeKey с версионированной canonicalization. `a->b` — подпись. Первый selector — exact edge_id или точная пара сервисов с optional операциями; glob и сложное разрешение конфликтов отложены. Не разбирать произвольные имена через `:`/`->` как источник истины.

## Реестр

| ID | Формат | Производитель → потребитель | Владелец |
|---|---|---|---|
| P1 | OTLP и SourceCapabilities | Агент/стенд → B | B; A подтверждает источник |
| P2 | NormalizedSpan, TraceGraph, CallModel, Quality | B → C/D | B |
| P3 | EdgePolicy, EdgeStats и provenance | B/C → C/D | B+C |
| P4 | ConfigSnapshot | A/declared → B/C/D | A |
| P5 | Finding, Recommendation, ChangeIntent | C → A/D | C |
| P6 | Plan/Apply и operation status | UI D → backend A ↔ adapter | A; D — только UI |
| P7 | Snapshot, Product API и Journal | Backend A/B/C → frontend D/CLI | A:control, B:data, C:analytics; D:viewer |
| P8 | Scenario/Expected/EvalResult | A → C/D | A+C |
| P9 | ProjectionRequest/Result | B/пользователь → C → D | C |
| P10 | CohortRequest/Result | D/repository → C → D | C |

## P1. Входная телеметрия

Первый профиль: OTLP/HTTP `POST /v1/traces`, `application/x-protobuf`, официальный protobuf decoder. JSON/JSONL — удобные fixtures/import, включаются при реализации и указываются в capabilities. Поддержка gzip не объявляется до проверки: неподдержанное encoding отвергается явно, exporter настраивается соответственно. gRPC ingress, Tempo/Jaeger wrappers и дополнительные транспорты — позднее.

Предлагаемые начальные лимиты: 8 МиБ body, 10 000 spans/пакет, 2 000 spans/trace, глубина обхода 256. Значения фиксируются на старте команды и публикуются. Превышение не обрезает данные тихо. Если поддерживается gzip, обязателен отдельный лимит распакованного тела.

Приём транзакционный для пакета. 200 после commit с корректным OTLP ExportTraceServiceResponse. Невалидные IDs/times →400, неподдержанный media/encoding →415, размер →413, временная нехватка capacity/недоступность записи →503. Дубликат принимается как успех без двойного счёта. Стандартные детали ответа/повторов сверяются с официальным OTLP; собственный product error JSON не подставляется вместо протокольного ответа.

Отсутствие `service.name`, method/route или error.type — diagnostic/coverage, не причина терять весь настоящий поток. End<start и некорректные ненулевые ID — недопустимые данные.

`SourceCapabilities`: source_id, connection_mode(existing_otlp/file_import/language_auto/ebpf), agent_name/version?, source_os/architecture?, kernel_version?, runtime/framework?, requires_launcher_change, requires_dependency_install, requires_restart, required_permissions[], protocols, propagation_verified, supported_fields[], missing_fields[], sampling(all_received/head/tail/unknown), known_losses, verified_at. Kernel/privileges заполняются только если нужны конкретному источнику; их отсутствие не блокирует OTLP/backend. OS удалённого источника не обязана совпадать с backend OS. Это профиль реальной проверки, не обещание универсальной поддержки.

Обязательный результат A: трасса с parent/client/server связями под конкуренцией. Желаемые признаки retries/error/idempotency не считаются обязательными для любого внешнего агента; при их отсутствии зависимые выводы ограничены.

## P2. Спаны, граф и вызовы

`NormalizedSpan`:

| Поля | Смысл |
|---|---|
| trace_id, span_id, parent_span_id | Фактические идентификаторы |
| service, instance_id?, service_version? | Идентичность и наблюдаемые resource dimensions |
| name, kind | internal/server/client/producer/consumer/unspecified |
| start_ns, end_ns, duration_ns | Duration=end−start; исходное время не исправляется скрыто |
| clock_domain | Идентичность часов по instance/resource или unknown |
| operation, target? | Нормализованная операция и назначение с source |
| otel_status, http_status?, grpc_status?, error_type? | Только наблюдавшиеся на этом span значения |
| resend_count? | `null` при отсутствии |
| idempotency | unknown/present/absent_observed; последнее требует явного основания |
| links_present, async_markers | Сохраняются для будущей применимости дерева |
| field_coverage, diagnostics | Какие признаки доступны и что потеряно |

Нормализатор сохраняет HTTP method/route/status, очищенный target/path, error.type, доступные resend/DB/gRPC/async признаки, service/version/environment/namespace/instance. Секретные заголовки, тела и query не сохраняются. Значения Idempotency-Key не записываются открыто; для первого показа достаточно наличия/unknown. HTTP-код SERVER не переносится в CLIENT по сравнению длительностей.

`TraceGraph`: trace_ref, first_received_at_ns, last_received_at_ns, assembly_state(open/quiet/forced), spans[], roots[], parent/children indexes, quality. Quiet не означает «полный trace». Missing server у внешнего получателя и missing parent — разные факты.

Дедуп по project+trace+generation+span ID. Идентичная доставка не меняет revision/TTL. Новый span дополняет trace и увеличивает revision; анализ пересчитывается по новой версии. Конфликт двух завершённых spans с одним ID — diagnostic, первый принятый не переписывается молча. Late parent может уточнить edge/call — старый вклад не суммируется с новым.

`Quality`: issues[{code,scope,span_ids,details}], orphan_count, conflicting_span_count, unknown_service_count, source_loss?, parse_errors, forced_reason?. Коды минимум: missing_parent, multiple_roots, cycle, depth_limit, cross_clock_uncertain, missing_field, conflicting_span, source_loss. Отсутствие issues не доказывает полноту.

`Attempt`: trace_ref, attempt_id=CLIENT span ID, edge_id, duration_ns, start_ns, server_span_id?, server_duration_ns?, caller_server_span_id?, upstream_attempt_id?, offset_in_caller_ns?, outcome, client_http_status?, server_http_status?, error_type?, resend_count?, call_id?, attempt_index?, gap_before_ns?, first_path?, classification_basis, confidence. First_path=true только для первой попытки вызова, у которой все известные попытки-предки также первые; при неопределённой группе/ancestry — null.

Outcome: ok/timeout/fast_fail/http_error/grpc_error/transport_error/error_unknown/outcome_unknown. Отсутствие ERROR само по себе не доказывает ok. Offset вычисляется только при подтверждённой совместимости часов.

`LogicalCall`: trace_ref, call_id, edge_id, owner_span_id?, attempt_ids[], hypothesis(single/retry/hedge/ambiguous), confidence, evidence_codes[], gaps_ns[]. call_id детерминирован внутри revision; поздние данные могут его изменить. Устойчивые ссылки используют TraceRef и исходные span IDs.

Явный resend/поддержанная retry-обёртка сильнее эвристики «ошибка→следующий вызов». Overlap сам по себе не доказывает hedge. Пять успешных последовательных вызовов — пять single. Неопределённая группировка не превращается в уверенный retry. Messaging исключается из синхронной модели, но факт async в графе сохраняется.

Будущие timing-показатели вычисляются через union интервалов на совместимых часах. «Вне непосредственных детей» и «вне логических исходящих вызовов» — отдельные величины; ни одна не CPU-время.

## P3. Рёбра и политики

`Sourced<T>`: value|null, source(runtime/declared/annotated/cluster/observed/unknown), confidence, sample_count, config_revision?, observed_at_ns?, stale, reasons[]. Источники runtime/declared/inferred доступны отдельно; effective-поле указывает стратегию выбора. Предложение первого профиля: свежий runtime → declared → осторожная inference. Это требует согласования; конфликт не скрывается.

`EdgePolicy`: edge_id, per_attempt_timeout_ns:Sourced<duration>, max_attempts:Sourced<int>, backoff{kind:fixed|exponential|unknown, base_ns, multiplier, cap_ns?, jitter:none|full|unknown}, retry_on[], policy_scope, effective_source. Не наблюдались ретраи — observed_max=1, но configured_max неизвестно.

`EdgeStats`: edge_id, dataset_ref, attempts, eligible_calls, ambiguous_calls, trace_count, known_outcomes, unknown_outcomes, errors, attempt_error_ratio?, success_p50/p95/p99_ns?, gaps_by_index, jitter_cv?, coverage, sampling. Ratios содержат знаменатель. Idle/no samples →null, не 0% ошибок.

Перцентили: linear interpolation по позиции q×(n−1), округление результата к ns half-up; число наблюдений рядом. Доли отражают принятую выборку, не всю систему при tail sampling/потерях. Здоровая база и широкий policy inference — после первого показа.

## P4. Конфигурация

`ConfigSnapshot`: config_id, schema_version, source(runtime/declared), service:ServiceKey, revision, fetched_at_ns, freshness, external_timeouts[], edges[], capabilities.

ConfiguredEdge: edge selector, call_ref, per_attempt_timeout_ns?, max_attempts?, backoff?, retry_on?, notes[]. Полный профиль также содержит ServiceSettings{on_client_gone:continue|cancel|unknown,propagate_deadline:bool|null}, idempotency_support и отдельно lab work/chaos с пометкой lab_only. Последние служат справочному UI/eval, а не подаются в trace-only правила или projection как ответ о причине. Все известные значения отражают настройки, а не ground truth о выполнении. Snapshot отдельно от OTLP: анализатор знает, что часть вывода основана на конфиге.

Stand `GET /_config` возвращает этот формат. Runtime revision изменяется при реальной мутации. Stale/unavailable конфиг не выдаётся за свежий. `call_ref` — стабильный ID вызова в конфиге стенда, а не URL из span и не call_id отдельного trace.

## P5. Находки и рекомендации

`AuditResult`: result_id, dataset_ref/hash, model_version, ruleset_version, config_refs[], thresholds_version, sampling, findings[], rule_evaluations[], generated_at.

`Finding`: finding_id, occurrence_id, rule_id/version, edge_id, severity(low/medium/high), confidence, basis(observed/inferred/config_aware), title, explanation, evidence, affected_count, denominator_count, affected_ratio?, exemplars≤5, recommendations[].

Finding identity = стабильный hash(project+environment+rule+edge+variant). Occurrence дополнительно привязан к dataset и версиям входа. Apply не выбирает «текущую похожую рекомендацию» по одному finding_id; нужен конкретный recommendation/occurrence ref.

Evidence типизирован по правилу: formula_code, operands с единицами и provenance, measured_count, threshold, trace/span refs. Текст арифметики строится из operands, а не хранится единственным доказательством.

`RuleEvaluation`: rule_id, status(evaluated/insufficient_data/not_applicable/not_implemented), eligible_count, excluded_by_reason, triggered_count. Пустой список findings при insufficient_data не означает «всё исправно».

`Recommendation`: recommendation_id/version, kind(primary/alternative/temporary), text, why, risks[], preconditions[], intents[], feasibility(feasible/infeasible/unknown). Числовой эффект на пользователей не обещается без отдельной модели/измерений.

`ChangeIntent`: target{service,call_ref?}, expected_config_revision, set{per_attempt_timeout_ns?,max_attempts?,backoff?,retry_on?,on_client_gone?,propagate_deadline?,idempotency_enabled?}, reason_ref. В первом предложении разрешены только timeout/attempts/backoff/jitter и обязательный call_ref. Полный профиль разделяет service-level и call-level changes; каждый ключ типизирован и проверяется capability адаптера. idempotency_enabled допустим только при реальной семантике серверной дедупликации/заявленной лабораторной реализации, не как обещание от одного заголовка. Нет arbitrary commands/URLs и неограниченного dict настроек.

Первый приоритет — R4: локальный бюджет retry-цепочки. R1/R5 целевые при достаточных данных. Полная система реализует R1–R8/D1 по разделу10; до готовности rule_evaluations явно сообщает not_implemented.

### Арифметика R4

Для поддержанной последовательной retry-формы:

```text
need = N × per_attempt_timeout + sum(backoff_i)
fixed:       sum = (N−1) × base
exponential: sum = base × (2^(N−1)−1), если multiplier=2 и нет cap
```

Full jitter: upper bound пауз по настройкам отдельно от среднего/наблюдённой медианы. Cap и неизвестные параметры учитываются явно. Hedge/ambiguous в serial-формулу не подставляются.

Upstream budget берётся из известного per-attempt timeout/явного external timeout в согласованной области. Это локальная проверка, не доказательство общего SLA и не точный остаток deadline. Для рекомендации учитывается резерв/elapsed, если известен; при недостатке данных явно указывается условность. Budget≤0 или меньше минимально допустимого timeout →infeasible, без применимого патча.

Предлагаемые пороги для согласования: ≥5 наблюдений пары; high при need>1.2×upstream, medium при need>1.01×upstream. Источники обоих timeout обязательны. Пороговые значения фиксируются в thresholds.yaml/version и тестируются на стенде.

## P6. План и применение

Первый срез — один сервис или один исходящий вызов за операцию. Цель берётся из серверного реестра интеграций по окружению и идентификатору сервиса. Запись требует разрешения для цели и поддержки поля адаптером. Backend проверяет их при планировании и повторно при записи. План из occurrence_id/recommendation_id можно создать и применить только для цели на управляемом стенде. Попытка сделать это во внешнем окружении возвращает 403 recommendation_lab_only. Отдельный ручной план для разрешённого поля внешнего сервиса проверяется по обычным правилам доступа. Для исходящего вызова владелец конфигурации — вызывающий сервис.

| Ручка стенда | Запрос / ответ |
|---|---|
| GET /_health | service identity, ready |
| GET /_config | ConfigSnapshot |
| PATCH /_config | A-owned validated changes with `request_id` and `expected_revision`; service applies atomically and returns operation status/revision. |
| GET /_config/operations/{request_id} | Подтверждённый результат для reconcile |
| POST /_chaos | Только lab-настройки ошибки/задержки |
| DELETE /_chaos | Сброс lab-сбоя |
| POST /_shutdown | Graceful stop с drain/flush |

Plan валидирует все значения/поля до мутации. CAS = сравнить expected revision и изменить атомарно; mismatch→409 без частичного изменения. Request ID с тем же содержимым возвращает прежний результат; с другим→409. Неизвестное поле не игнорируется.

Product backend сохраняет pending journal **до отправки apply**. Status: planned/applying/applied/no_op/rejected/failed/unknown. Timeout после отправки — unknown, неfailed с безопасным слепым повтором. Reconcile опрашивает operation и конфиг. Readback подтверждает фактическую revision/values.

Межсервисная транзакция и автоматический rollback не включены в первый профиль. Для будущих многошаговых операций обязательны partial/unknown и отдельный CAS-план отката.

Для генерации стенда первый профиль принимает ссылку на конкретную ревизию внешней трассы (окружение, trace ID, generation, revision) и выбранную последовательную цепочку. B отдаёт проверенную упорядоченную цепочку из графа либо причину отказа. A строит версионированный лабораторный сценарий с локальными целями, исходной ссылкой, наблюдаемыми параметрами и явными ручными настройками. Для согласованного первого checkout стенд доступен напрямую по `scenario_id`; trace-to-scenario генератор остаётся последующим этапом. Прогон привязан к версии сценария. Поздние спаны обновляют внешнюю трассу, а созданный сценарий сохраняет свой исходный снимок. При неполной или неоднозначной цепочке генератор указывает, какие связи надо уточнить.

## P7. Продукт и журнал

`Environment` содержит `environment_id`, имя и тип `external|lab`. Источник привязывается к окружению на стороне сервера. Трассы, агрегаты, рекомендации, планы и журнал читаются в контексте выбранного окружения. Внешние трассы хранятся отдельно от трасс стенда, включая случаи совпадения имён сервисов. Окружение определяет контекст данных; управление конфигом определяется по цели.

`ServiceCapabilities` содержит `read_config`, `write_config` и `apply_recommendation` по сервису и доступным полям. Итоговые возможности учитывают настройки пользователя и поддержку адаптера. apply_recommendation всегда false для внешнего сервиса, даже если отдельные поля доступны для ручного редактирования. По умолчанию сервис доступен для наблюдения. Конфигурация читается и меняется через зарегистрированный адаптер, например управляющий HTTP API или доступный серверу механизм чтения и обновления файла с поддержкой перечитывания. Ручное изменение и рекомендация используют общий механизм проверки версии, применения и журнала P6. `EnvironmentCapabilities` отдельно сообщает о лабораторных возможностях `inject_fault` и `run_workload`.

Лабораторный fault задаётся для входящего route: probability per request, HTTP status, дополнительная delay и execution boundary (fixed либо random среди помеченных точек). Нормальная работа маршрута задаётся независимо как fixed или lognormal с p50/p99. Лабораторный прогон содержит `run_id`, `environment_id`, сценарий, ссылки на активные версии настроек и состояние. Связь трасс с прогоном передаётся в телеметрии стенда и сохраняется B. Сравнение до и после опирается на два прогона одного сценария, показывает число наблюдений и отдельно отмечает результат записи конфига и наблюдаемый результат.

Первый product API:

```text
GET  /api/v1/capabilities?environment_id=
GET  /api/v1/traces?environment_id=&query=&service=&status=&method=&span_id=&start_from_ns=&start_to_ns=&sort=&limit=&offset=&include_findings=
GET  /api/v1/traces/{id}?environment_id=&generation=&revision=&include_findings=&include_span_kind=
GET  /api/v1/audit?environment_id=&view=&defer=&job_id=
POST /api/v1/change-plans
POST /api/v1/change-plans/{id}/apply
GET  /api/v1/journal?environment_id=
GET  /api/v1/journal/{id}?environment_id=
GET  /api/v1/report
GET  /healthz
```

Для текущего фронтенда эти маршруты возвращают UI DTO в envelope P7. Маршрут /state зарезервирован для полного Snapshot. /audit отображает одну основную рекомендацию в полях recommendation и recommendation_id из списка P5. Evidence имеет kind=retry_budget для R4 с известным конфигом или kind=trace_observation для выводов по трассам. Для trace_observation требуется source, поля бюджета и ревизии отсутствуют. Ссылки на примеры аудита и элементы списка трасс содержат полный TraceRef. Детали с generation и revision возвращают ровно запрошенную ревизию или 404. Если обе версии опущены, возвращается текущая ревизия в указанном окружении. Передача только одной версии возвращает 400.

В деталях трассы `spans[].target` — необязательная строка для клиентского спана, например `payment` из OTLP-атрибута `server.address`. Если парный серверный спан отсутствует, фронт показывает адресат отдельным узлом зависимости и использует длительность клиентского спана на ребре. При наличии парного серверного спана используется идентичность сервиса из него. Время работы сервиса рассчитывается по его серверным спанам. Поле `target` не используется для привязки управляющего API.

`include_findings=true` по умолчанию добавляет результаты аудита для точных TraceRef. При `include_findings=false` списки и детали читают телеметрию без запроса управляющих конфигураций и выполнения правил. Поле `finding_count` сохраняет форму ответа и равно 0, но в этом режиме не отражает результат аудита. Детали не содержат `finding_occurrence_ids`. Фильтры, пагинация и требования к точной ревизии действуют в обоих режимах. Иные значения возвращают `400 invalid_include_findings`.

Параметр `include_success_rates=true` добавляет в список `success_rates` с ключами `5`, `30`, `60`, `180` (минуты). Для каждого периода сервер считает `ok / (ok + error)` по времени начала трейса во всём выбранном окружении, независимо от фильтров и страницы. Трейсы с неизвестным исходом или временем начала исключаются. Если известных исходов нет, значение равно `null`.

Карточка трейса запрашивает детали с `include_span_kind=false`. В этом ответе ключ `spans[].kind` отсутствует. По умолчанию API передаёт `kind` для клиентов, которым он нужен для анализа зависимостей. Иные значения возвращают `400 invalid_include_span_kind`.

Детали могут содержать `compactable_client_spans`, словарь из Span ID исходящего спана в Span ID его единственного дочернего серверного спана другого сервиса. В словарь входят синхронные HTTP-вызовы с одинаковым кодом 2xx или 3xx и без ошибок на обеих сторонах. Полный список `spans` доступен в том же ответе.

`GET /api/v1/audit?defer=true` запускает расчёт или присоединяется к уже выполняющемуся для того же окружения, представления и режима. До готовности API возвращает `202` с envelope `zerotrace.audit-job` и payload `{status: "pending", job_id}`. Клиент опрашивает тот же маршрут с `job_id`. Готовый результат — обычный `200 zerotrace.audit`. Чужой или вытесненный идентификатор возвращает `404 audit_job_unavailable`, заполненная очередь — `503 audit_busy`. Синхронный контракт сохраняется при отсутствии `defer=true`.

`GET /api/v1/traces` принимает необязательный `method` с точным совпадением по HTTP-методу входной операции без учёта регистра. Например, `query=/checkout&method=POST` выбирает POST среди совпадений поиска по `/checkout`. Некорректный HTTP-токен возвращает `400 invalid_method`. Метод и остальные фильтры применяются до сортировки и пагинации. `total` и `outcomes` учитывают совпавшие строки. Ответ с фильтрами или сортировкой содержит `methods` с уникальными наблюдаемыми методами выбранного окружения в верхнем регистре и в алфавитном порядке, независимо от фильтров и страницы.

`TraceSummary.http_method` содержит метод в верхнем регистре или `null`, если он неизвестен. `endpoint` содержит путь без метода или имя операции, когда HTTP-метод неизвестен. `operation` сохраняет исходное нормализованное представление для существующих клиентов.

Дополнительные параметры поиска `GET /api/v1/traces`:

| Параметр | Семантика |
|---|---|
| `span_id` | Точное совпадение с любым спаном текущей ревизии в выбранном окружении. Формат: 16 шестнадцатеричных символов в нижнем регистре, хотя бы один ненулевой. Если ID встретился в нескольких Traces, список возвращает все совпадения, по одной строке на Trace. |
| `start_from_ns` | Включительная нижняя граница времени начала Trace (`TraceSummary.start_ns`), неотрицательные десятичные наносекунды от Unix epoch в UTC в диапазоне int64. |
| `start_to_ns` | Исключительная верхняя граница того же времени. |

Любая граница периода может быть опущена. При двух границах `start_from_ns < start_to_ns`. Некорректный ID даёт `400 invalid_span_id`, некорректная граница — `400 invalid_start_from_ns` или `400 invalid_start_to_ns`. Обратный или пустой интервал даёт `400 invalid_time_range`. При заданном периоде Traces с неизвестным временем начала исключаются из выборки. Фильтры по спану, периоду, окружению и существующим параметрам объединяются через AND. Фильтры применяются ко всей выборке до сортировки и пагинации. `total` и `outcomes` считаются по совпадениям. Без новых параметров действует существующая семантика списка.

List limit≤100, сортировка по first_received_at DESC и trace_id; offset-pagination не гарантирует snapshot-consistency при новых данных — это явно допустимое ограничение первого UI. Сложные cursor snapshots позже. Trace-view и результаты содержат revision, старая конфигурация/selection на apply получает409.

Product errors: `{error:{code,message,details,retryable,request_id}}`; 400 синтаксис,404 ref unavailable,409 stale/conflict,422 семантика,413 limit,503 временный отказ. Не отвечать200 с error payload.

Snapshot: snapshot_id, dataset_ref/hash, generated_at, services/edges, quality, sampling, policies, findings, rule_evaluations, journal_summary, capabilities. Все таблицы одного snapshot согласованы по dataset/config versions. Детали spans выбранного trace загружаются отдельно.

JournalEntry: operation_id, plan_hash, finding/occurrence/recommendation refs, target, requested_at, status, actual_changes, pre/post_config_refs, error?, verification. В публичном списке для UI используются operation_id, target (отображаемая строка), requested_at, status, actual_changes (массив строковых field/before/after) и verification (статус или null до начала проверки). У незавершённой операции actual_changes может быть пустым. Journal переживает restart. Незавершённая операция сверяется до новой мутации того же target.

Verification отдельно: checking/insufficient_data/symptom_absent/symptom_present/inconclusive. Предлагаемый старт:20 с grace и≥30 пригодных вызовов затронутого ребра; не30 любых traces. Нужны coverage и сопоставимые условия; ошибки/latency рядом. Нет трафика→insufficient_data. Исчезновение finding не называется исправлением первопричины. Данные до apply не удаляются; конфигурационные epochs стенда хранятся отдельно от правила вывода.

JSON-report содержит schema/producer/model/rules/config/threshold versions, input refs/hash, quality, limitations и результаты. Offline HTML и what-if API — последующие задачи, не часть первого обязательного профиля.

## P8. Сценарии, ожидаемые результаты и fixtures

Scenario: schema_version, scenario_id/version, seed, topology_ref, workload_id, source_profile, load{entry,rps,duration_ns,timeout_ns,max_inflight}, timeline[], drain/flush policy, expected_ref. Сервисные конфиги и скрытое место fault не доступны trace-only анализу.

Сценарии первого среза:

- s00_healthy: нормальный бюджет, обязательный negative.
- s01_budget: известный плохой serial budget, R4 с конфигом, исправляемый параметр.
- s02_late_duplicate: повтор пакета и late parent, стабильный счёт и новая revision.
- s03_apply_conflict: stale config и идемпотентный request ID.
- Дополнительно R1/R5, только после подтверждения полей источника.

Expected: schema_version, scenario/version, mode(trace_only/config_aware), required_capabilities, must_find[], must_not_find[], policy_assertions[], apply_assertions[], quality_assertions[], unexpected_policy(review/fail). Для каждой находки rule/version, selector, severity bounds и числовые operands/tolerances. Expected пишет A, C проверяет scorer; конфиг для config-aware явно передаётся отдельным входом.

EvalResult: scenario/build/seed refs, passed/failed/blocked assertions, actual evidence, unexpected/unlabelled findings, trace refs, load summary, artifacts. Precision/recall только по размеченным выводам; «лишняя» неразмеченная находка не считается автоматически корректной или FP. Полный statistical eval после первого показа.

Обязательные fixtures на стыках:

```text
fixtures/otlp/        s00, s01, late_duplicate
fixtures/config/     s00, s01, stale_revision
fixtures/model/      s00, s01, ambiguous_calls
fixtures/findings/   s00, s01, insufficient_data
fixtures/product/    snapshot, plan, applied, unknown, journal
```

Первый набор B/C/D создают вручную по согласованным контрольным числам, затем A добавляет реальные дампы агента. Изменение golden сопровождается причиной; старый прототип не является безусловным oracle.

## 9. Минимальная проверка контрактов

IDs/ns roundtrip; CLIENT/SERVER status; missing field→unknown; duplicate/late revision; cycle/depth; commit failure; healthy/bad/infeasible R4; exponential pauses; independent repeated calls не retry; plan validation; CAS conflict; lost response→unknown/reconcile; restart journal; no traffic→insufficient_data. Детальные тесты и порядок — в файлах A–D.

Ни один из перечисленных форматов не означает, что соответствующий endpoint уже реализован. Расширенный будущий профиль согласуется отдельно; схема 1.0.0 фиксируется после архитектурного решения.

## 10. Полный реестр правил и рекомендации

Все пороги — в Thresholds с schema/version, defaults и overrides по структурированным selectors. Предлагаемые стартовые значения: min_cases5, tolerance=max(20ms,5%duration), R2 tolerance=max(20ms,2%duration), amp_medium4/amp_high9, no_backoff_p95<5ms, jitter_cv<0.05 при≥10 паузах. Это калибровка прежнего стенда, не универсальная гарантия; override не меняется тайно между пересчётами.

| ID | Проверка полной системы | Ограничение и рекомендация |
|---|---|---|
| R1 | Ошибочный CLIENT и более длительный связанный SERVER за допуском | Надёжная пара/quality; duration difference не CPU и не точное время после disconnect. Диагноз degraded/misconfigured/slow — гипотеза по baseline; увеличить timeout можно только с проверкой бюджета, отмена работы — защитная мера |
| R2 | Downstream попытка начата после upstream wait относительно начала caller SERVER | Только пригодная ancestry и same-clock offset; передавать оставшийся deadline/не начинать позднюю попытку. Unknown не превращать в срабатывание |
| R3 | Усиление retry-попыток вдоль реальных ancestry paths | First-path baseline определён; fanout/loop не retry. Факт и theoretical bound отдельно; показывать deepest affected edge с upstream refs. Рекомендация сократить уровни retries с условиями |
| R4 | Несходящийся локальный бюджет serial вызова | Формула выше, источники timeout/attempts известны; numerical recommendation feasible или явный отказ |
| R5 | Почти нулевые serial retry gaps либо малая вариативность | Достаточно уверенные retry-группы; CV описывает наблюдение, не доказывает настройку jitter. Exponential/full jitter по поддержанному adapter |
| R6 | Риск повтора POST/PATCH при неподтверждённой идемпотентности | Не видеть ключ в spans не значит отсутствия ключа/бизнес-дедупликации. Проверить поддержку или отключить retry; не обещать двойное списание как установленный факт |
| R7 | Повтор после статуса, не разрешённого принятой retry policy | Только CLIENT-статус. Исходный HTTP default:4xx кроме408/425/429; gRPC exclusions версионированы. Проверить/сузить retry_on |
| R8 | Нижний per-attempt timeout≥верхнего в пригодной паре | Локальное свойство известных политик, не end-to-end SLA; уменьшить при feasible budget или пересмотреть deadline |
| D1 | Runtime/declared расходится с наблюдаемой оценкой | Сопоставимы version/workload/freshness; показать оба источника, не заменять одно другим |

Finding affected_ratio считается от подходящих traces с этим edge; eligible/excluded denominator публикуется. Severity/score не смешивается с gain what-if. Предлагаемый score как в старом плане: severity_weight(3/2/1)×(0.5+affected_ratio)×confidence_weight(1/0.7/0.4); неизвестная доля не подставляется0, сортировка по severity/confidence/ID с причиной. Это порядок показа, не вероятность инцидента.

`EdgeBaseline`: baseline_id/version, edge_id, environment, caller/callee versions, workload_id?, source_window_refs, samples, success_p50/p95/p99_ns, error_ratio, sampling, created_at, valid_until?, state(reference/learning/stale). Не обучать из tail-selected/unknown coverage, не смешивать разные версии молча. Reference не обновляется бесконечным пошаговым rolling×1.5; смена эталона фиксируется отдельной revision.

`ServiceTiming`: service/operation/kind(server/client), sample_count, total_p50/p95/p99_ns, uncovered_direct_children_p50/p95/p99_ns?, uncovered_outgoing_calls_p50/p95/p99_ns?, coverage. Client-only — со стороны вызывающего; неизвестное server own-time=null.

`Amplification`: callee, trace_count, avg/p95_attempts_per_trace, denominator_policy, sampling. Отличается от retry amplification R3: обычный бизнес-fanout может давать большую величину без нарушения retries.

## 11. Полный профиль хранения, окна и источников

TTL с первого успешного приёма trace; поздние spans/повторный импорт не продлевают срок. Истёкший trace можно импортировать как новое generation; старые refs не начинают ссылаться на новое содержание. Новые spans revision+1; unchanged imports не увеличивают revision. User deletion удаляет graph/cache и активный contribution, правила сохранения обезличенных агрегатов согласуются отдельно.

Live membership по first_received_at, полуоткрытое окно[now−window,now). Late revision не переносит trace в новое окно. Contribution keyed(project,trace,generation), новая revision заменяет старую. Event-time batch режим отдельный и подписан. Quiet timeout5с — стартовое предложение, неcomplete; forced close при лимите виден.

При независимых workers запись trace и outbox event атомарна, consumers идемпотентны по input refs+versions. Устаревший job не перезаписывает новый snapshot. Cache key включает нормализацию/grouping/rules/threshold/config/baseline versions. SQLite writer и очереди ограничены; disk budget учитывает основной файл,WAL/SHM и резерв; RAM ограничивается отдельно. TTL/лимиты72ч/500МиБ — предложенные defaults до измерений.

Полный P1: OTLP HTTP/protobuf+JSON, gzip с лимитом распаковки, JSONL import/export. Tempo import — отдельный адаптер: server-configured source_id/URL, trace ID32hex, wait_seconds0..10. Поддерживает protobuf-JSON base64 IDs/enums и wrappers trace/batches; Jaeger result/legacy scopes — только объявленные adapters, не стандартный OTLP wire format. Stabilization не доказывает полноту; поздние spans merge идемпотентно. Экспорт сообщает normalization profile и потерянные поля, не объявляется lossless raw archive.

Весь доступный поток →аналитика/агрегаты →при необходимости архив «проблемы+независимые healthy samples». SamplingContext(mode,probability?,source,policy_version,limitations) хранится в результатах. Отобранные traces не источник несмещённых population percentages. Общие входные ошибки считаются по entry selector, не по сумме всех CLIENT+SERVER операций.

## P9. Моделирование ускорений ZeroTrace

```text
ProjectionRequest:
  trace_ref, root_span_id?, changes[{span_id,speedup_percent:0..100}],
  algorithm_version

ProjectionResult:
  trace_ref, algorithm_version, input_hash, status,
  applicability{reason_codes,scope_root,excluded_span_ids},
  requested_selection, effective_selection, selection_notices,
  baseline_ns, projected_ns?, gain_ns?, gain_ratio?,
  spans[{span_id,depth,offset_ns,observed_duration_ns,
         uncovered_direct_children_ns,projected_duration_ns?,limiting}],
  candidates[{span_id,target,gain25_ns,gain50_ns,gain100_ns}],
  limiting_branches[], observations[{kind,message,span_ids}], assumptions[], warnings[]
```

Алгоритм `interval-blocks-v1.0.0`: parent-child дерево; прямые дети сортируются по start/end/id; перекрывающиеся интервалы объединяются в blocks; промежутки вне union сохраняются; последовательные blocks сдвигаются; внутри overlap block сохраняются наблюдаемые смещения старта; окончание=max(offset+projected child duration). Выбранная ветка сокращается целиком с потомками; результат пересчитывается вверх.

До32 изменений, UI до8; дубли span недопустимы. Если выбраны предок и потомок, предок включает потомка, вложенный выбор исключается с notice. Root не выбирается как сокращаемая внутренняя ветка. Unknown ref→404/422, устаревшая revision→409. Явный root ограничивает поддерево; наблюдения и ranking не выходят за его scope.

Gates: цикл/глубина>256, неоднозначный root, relevant missing parents, несвязность, links/producer-consumer в scope, несоответствие фактических temporal bounds. Согласование часов обеспечивает пользователь источника; отсутствие или различие clock_domain само по себе не блокирует P9. Сервер использует переданные timestamps на общей временной шкале и явно указывает это допущение. Допуск500000ns; clipping только в модели, с diagnostic, исходные timestamps сохраняются. Отсутствие defects не доказывает полноту. unavailable→projected/gain=null; available gain0 — отдельный осмысленный результат.

Контрольные числа:1000∥800, первая−50%→800; sequential1000+800, первая−50%→1300; parallel обе−50%→500. Offset900 второй ветки длительностью800 сохраняет 1700 даже после удаления первой1000. Ties показывают все limiting branches.

Candidate gains независимы и не складываются. Joint gain пересчитывается из общей selection. Нет HTTP-запросов к приложению/replay/apply при изменении процентов. Сокращение ветки не CPU-ускорение сервиса и не симуляция другой retry policy. Вывод явно projected при предположении ожидания всех детей и сохранения наблюдаемого порядка запуска.

Status=available/insufficient_data/not_applicable/unsupported. Наблюдения repeated_calls и possible_retry сохраняют осторожную семантику ZeroTrace: число/перекрытие/ошибка→успех с подтверждающими span IDs не доказывают избыточность или намерение повторить. Они не обязаны совпадать с классификацией CallModel; отличие объясняется basis/confidence.

## P10. Сравнение групп ZeroTrace

`CohortRequest`: baseline_trace_refs[1..100], candidate_trace_refs[1..100], entry_selector, comparison_version. Группы непусты, без дублей и пересечений. Все refs читаются на согласованном repository snapshot; истёкший/пропавший trace не исключается молча.

Сопоставимость: project/environment/entry service/operation/workload. Unknown workload/environment→exploratory warning, не утверждение равных условий. Service versions могут различаться как исследуемая переменная и показываются. Совпадение признаков не гарантирует одинаковые данные/нагрузку.

`CohortResult`: input_refs/hash, algorithm_version, comparability/status, operation, baseline/candidate{count,root_latency_p50/p95_ns,root_error_ratio?,internal_error_trace_ratio,direct_calls[],overlap_ratio?,coverage,sampling}, delta_ns, delta_ratio?, observations[], warnings[].

DirectCallStats: operation/edge, median_calls_per_trace, pooled_median_duration_ns, sample_count. Ровно прямые исходящие вызовы выбранной entry boundary; вложенные уровни не смешиваются. Отсутствующий вызов даёт0 в count per trace; неизвестный исход не даёт0 error. Overlap ratio=сумма durations/union duration, может быть>1, это не процент.

Root200 после internal503/retry — успешный вход при наличии внутренних ошибок. Sample-p95 малой группы помечен нестабильным; единая percentile policy изP3 применяется ко всему продукту (осознанное отличие от nearest-rank старого Go-кода).

Наблюдения: изменение количества вызовов, длительностей, перекрытия, внутренних ошибок, с конкретными числами и refs. Вывод описательный, не причинное доказательство релиза/сервиса.

## 12. Полный Product API и отчёт

К P7 добавляются:

```text
POST /api/v1/import                     # OTLP JSON/JSONL
POST /api/v1/sources/tempo/import       # source_id,trace_id,wait_seconds
GET  /api/v1/traces/{id}/otlp
DELETE /api/v1/traces/{id}              # expected generation/revision
GET  /api/v1/services
PATCH /api/v1/services/{id}             # display metadata с revision
GET  /api/v1/storage
POST /api/v1/projections                # P9
POST /api/v1/comparisons                # P10
GET  /api/v1/reports/{id}
POST /api/v1/lab/capture                # один явный запрос при lab capability
```

Полный поиск охватывает период, входной сервис, эндпоинт, HTTP-метод, длительность, результат запроса, Trace ID и Span ID. Профиль и workload доступны при наличии данных. Сортировка до пагинации, tie поID. Полный cursor содержит query+repository snapshot; cursor expired→409. Выбор cohorts сохраняется между страницами; stale refs проверяются при анализе.


`ReportBundle`: schema/producer/normalization/model/rules/projection/comparison/threshold versions, input refs+hashes, snapshot, selected graph, audit/projection/comparison results, journal refs, sampling, quality, assumptions/limitations. Offline HTML вшивает локальные JS/CSS и JSON с безопасным экранированием; никаких CDN/мутаций. Текст span не вставляется как HTML. JSON и HTML несут одинаковую семантику.

CLI: audit,collect,serve,project,compare,eval,export;0 успех,1 input/runtime/eval failure,2 severity threshold,3 insufficient required data. What-if не запускает stand. Независимый controlled validation запускается отдельной явной командой eval.

Lab capture принимает только заранее зарегистрированные scenario/profile IDs, не произвольный URL/метод/тело из браузера; возвращает trace ref, фактический результат запроса и readiness/quality. Параметры what-if не являются командой capture. SDK-профиль с ручными spans независимой лаборатории допустим как отдельно подписанный источник; он не выдаётся за автоинструментацию без правки исходников. Установленный языковой auto-agent может использовать SDK внутри runtime, не требуя SDK-вызовов в исходном коде приложения.

## 13. Платформенный контракт и способы подключения

Целевые native платформы backend/CLI — Windows, macOS, Linux. Приложение должно стартовать без Docker, Linux VM, eBPF, root, /proc или systemd. Браузерный UI работает с одним HTTP API независимо от ОС сервера. Требование подтверждается CI/запуском; пока нет реализации, это целевая поддержка, не результат теста.

OTLP/file import — базовая независимая граница. Языковые agents/auto-instrumentation подключаются установкой зависимостей и настройкой запуска/env/JVM flags, без изменения исходников. OBI/eBPF остаётся optional Linux adapter. Ни один источник не обязателен для старта продукта и не получает управляющих полномочий автоматически.

Собственные Python модули используют portable paths/process/network/SQLite. Unix-specific оптимизация допустима только как optional с корректным fallback. Native launcher должен иметь одинаковую семантику ports/data-path/stop/flush на трёх ОС. Для реальных источников отдельно опубликована матрица OS×runtime×library×protocol×connection_mode×tested capabilities; поддержка backend OS не доказывает поддержку любого языка на ней.

P7 capabilities содержит product_platform, supported_api_versions, enabled_features, sources[], adapter_capabilities, limits. Disabled eBPF не отключает trace import/audit/UI. Source health, отсутствие трафика и data-quality разные поля.

## 14. Единоличное владение backend API

D — frontend, не владеет серверными handlers или persistence. A собирает общий app/server из routers A/B/C. Три backend роли работают над одним Python приложением.

| Endpoint/подсистема | Backend-владелец | Клиент/зависимость |
|---|---|---|
| OTLP POST /v1/traces, import, traces/detail/delete/export | B | Источники A и frontend D |
| Services/display metadata/search/storage | B | Frontend D; shared repository для A/C |
| Sources/Tempo HTTP adapter, health/capabilities/server startup | A | Данные в ingest B, UI D |
| ConfigSnapshot, change-plans, apply, journal, lab capture/chaos | A | Recommendation C, repository B, UI D |
| Извлечение цепочки для стенда и фиксация выборки по run_id | B | A задаёт срок сбора и управляет состоянием прогона; C читает зафиксированную выборку |
| Audit/state, policies presentation, projection/comparison | C | Метаданные прогонов A, зафиксированные выборки B, UI D |
| ReportBundle/JSON/HTML assembly, analytics CLI | C | Viewer/assets D, refs B и journal summary A |
| Native launch/CI/package/container profiles | A | Tests/контракты каждого владельца |
| Browser routes/API-client/state/forms/viewer/UI tests | D (frontend) | Готовые контрактные responses A/B/C |

P7 — общий пользовательский стык, а не серверная работа фронтендера. Каждый backend router имеет fixtures/OpenAPI и error examples до интеграции; D не ждёт завершения всех алгоритмов и работает по мокам. Verification scheduler — A; чистый evaluator симптома — C; хранение/read refs — B.

## 15. Кроссплатформенная приёмка

CI native Windows/macOS/Linux: dependency install, server/CLI start, health, OTLP ingest, SQLite save/restart/migrations, paths с пробелами, temp/permissions/locking errors, graceful stop и чистые analytics tests. UI build без обязательного bash; browser smoke и offline viewer проверяются отдельно. Source auto-instrumentation проверяется на реальных комбинациях, включая конкурентную связанную цепочку; неподдержанный профиль не объявляется универсальным.

Первая демонстрация 2–3 дня не означает все три ОС уже проверены: публикуются actual tested и target. Полный статус кроссплатформенного продукта присваивается после прохождения native матрицы; optional Linux/eBPF tests не блокируют native-профили других ОС.

## 16. Именные владельцы контрактов

| Роль | Участник | Стык |
|---|---|---|
| A | Никита Логвиненко, @n_logvi | P4/P6, integrations/control/server/infra |
| B | Михаил Гончаров, @mgoncharov | P1–P3, shared schemas/repository/data API |
| C | Амир Гараев, @garaevamir | P5/P9/P10, analytics API/Snapshot/reports |
| D | Агаев Хамза, @blxssgod | Frontend-потребитель P7/P9/P10, координация команды |

Сегодня, 10 ноября 2026, сначала фиксируются минимальные trace/model/config/finding/plan fixtures для сквозного пути; изменение producer-схемы сопровождается примером и ревью потребителя. Назначение задач не является отчётом об их выполнении.


## P11. Согласованный рабочий срез для первого показа

> Актуальные решения, уточняющие старые примеры P11, собраны в [configuration-and-lab.md](../architecture/configuration-and-lab.md). Это решения архитектуры, а не заявление о завершённой реализации или проверках. Полные требования к системе, включая native-переносимость, остаются в разделах выше.

### Актуальные решения первого показа

- Docker Compose — основной путь разработки и первого запуска. Базовый `up` поднимает только ZeroTrace; внешний девятисервисный checkout и `demo-load` включаются профилем `demo`.
- Лабораторный checkout создаётся из готового сценария через `POST /api/v1/lab/stands` с `scenario_id`. Генератор по трассе — отдельный последующий этап; его начальный линейный путь достаточен, генерация параллельных групп опциональна.
- Планирование, проверка прав, журнал, запись и readback принадлежат A целиком. Сервисный adapter API: `GET /_config`, `PATCH /_config`, `GET /_config/operations/{request_id}`. Отдельные stand-owned plan/apply ручки не добавляются. Запуск прогона несёт `request_id` для идемпотентности.
- Снимок конфига хранит revision по каждому сервису (`config_revisions`), а прогон запоминает все ревизии и снимки. Запуск проверяет карту целиком.
- Fault задаётся на входящем route: вероятность срабатывания на запрос, HTTP status, дополнительная задержка и граница исполнения. Граница может быть фиксированной или случайно выбранной из явно отмеченных точек. Нормальная работа маршрута поддерживает `fixed` либо `lognormal` с настраиваемыми p50/p99; вызовы downstream добавляются к времени выполнения.
- Нагрузка задаётся числом запросов и максимальной concurrency, с необязательным seed. Фактический seed сохраняется в run.
- Фактический build deadline текущего checkout — 20 секунд для девяти процессов. UI при потерянном ответе восстанавливает состояние через GET active.


### Подключение и доступ

- При старте сервер загружает реестр источников и сервисов. Каждый экспортёр получает отдельный токен в OTLP HTTP-заголовке Authorization. Сервер сопоставляет токен с source_id и environment_id. Внешний источник и стенд используют разные токены. Без известного токена пакет отклоняется. Токены хранятся в настройках сервера и экспортёра.
- Сервис на графе идентифицируется по environment_id, service.namespace и service.name. Для внешнего адресата, видимого только по клиентскому спану, используется отдельная идентичность зависимости; автоматической привязки адресата к управляющему API нет. Никита явно связывает ключ управляемого сервиса с адресом и разрешёнными полями адаптера. Если ключ неоднозначен, управление отключается до исправления реестра.
- Для первого показа один локальный оператор управляет зарегистрированным демо-сервисом и стендом. Управляющий API слушает loopback, интерфейс работает с ним на одном origin, браузерные запросы записи с чужого origin отклоняются. Если приём OTLP нужен по сети, внешний вход публикует только маршрут приёма телеметрии. Для удалённого или многопользовательского запуска запись внешних конфигов выключена, пока не определены аутентификация оператора и модель прав. UI получает итоговые возможности, а сервер повторяет проверку перед записью.

### API и примеры данных

JSON ниже показывает payload внутри стандартного ответа P7. GET в выбранном окружении использует environment_id в пути или query, как указано в таблице. POST изменения используют привязанные к окружению идентификаторы A-owned операции или стенда. Все идентификаторы в примерах условны.

| Запрос | Вход и результат |
|---|---|
| GET /api/v1/environments | Список окружений, например [{"environment_id":"shop","kind":"external"},{"environment_id":"sandbox","kind":"lab"}] |
| GET /api/v1/services?environment_id=shop | Ключ сервиса, отображаемое имя и возможности. Для orders пример {"service_id":"orders","read_config":true,"write_fields":["max_attempts"],"apply_recommendation":false}. У стороннего payment write_fields пуст, read_config и apply_recommendation равны false. |
| GET /api/v1/services/{id}/config?environment_id=shop | Продуктовый снимок с call_ref, значениями и revision. Отдельно service adapter использует `GET /_config`; права применяет A. |
| Планирование и применение | Выполняются целиком в A; прямые операции с сервисом идут через его adapter `PATCH /_config`, а подтверждение — через `GET /_config/operations/{request_id}`. Операция идемпотентна по request_id. Это заменяет старую схему stand-owned plan/apply. |
| POST /api/v1/lab/stands | Первый checkout путь принимает `scenario_id`; генерация сценария из `source` подключается позднее. Девять сервисов стартуют под супервизором; текущий build deadline 20 секунд. При потере ответа UI читает GET active. |
| GET /api/v1/lab/stands/active | Возвращает 200 с payload null, если стенда нет. Иначе возвращает stand_id, environment_id, source (точный TraceRef), status (building/ready/stopping), scenario_revision, fault_revision, config_revisions, active_run_id и latest_run_id. Пока стенд не готов, ещё не назначенные версии и идентификаторы равны null. |
| DELETE /api/v1/lab/stands/{id} | Останавливает стенд. После завершения процессов отвечает 200 с payload {"stand_id":"lab-1","status":"stopped"}. Повтор для уже остановленного стенда возвращает тот же ответ. |
| PUT /api/v1/lab/stands/{id}/fault | Входящий route задаёт probability, `http_status`, дополнительную задержку и boundary (fixed либо random из отмеченных точек). Возвращает fault_revision; DELETE сбрасывает fault. |
| POST /api/v1/lab/stands/{id}/runs | Запрос содержит request_id, scenario/fault revisions, полную карту `config_revisions`, request_count, concurrency и optional seed. A сверяет все версии и возвращает 202 с run_id. |
| GET /api/v1/lab/runs/{id} | Состояние running/completed/failed, ссылки на конфиг и сбой, completed_requests и trace_collection со status (waiting/ready/partial), expected_count и received_count. |
| POST /api/v1/comparisons | Для первого показа вход {"baseline_run_id":"run-a","candidate_run_id":"run-b"}. Ответ содержит число наблюдений, задержки, ошибки и попытки для каждого прогона. |

Списки трасс, детали и аудит принимают environment_id. Запись в неподдерживаемое поле отклоняется. Сборка стенда при отсутствующей ревизии возвращает 404. Неподдерживаемая топология возвращает 422 с кодом и списком проблемных связей.

### Остановка стенда и конфликты

- Для первого показа доступен один активный стенд и один прогон одновременно. «Остановить» завершает процессы стенда и освобождает их порты. Трассы, прогоны и журнал сохраняются. Неизвестный stand_id даёт 404. Если завершение процессов не подтверждено, сервер возвращает 503 с code=stand_stop_unconfirmed; повтор DELETE продолжает остановку. С начала остановки стенд переходит в stopping и удерживает слот до подтверждения. В stopping доступны чтение состояния и повтор DELETE. Запуск, применение конфига и изменение сбоя возвращают 409 stand_stopping.
- Пока прогон выполняет HTTP-запросы, операции остановки, запуска второго прогона, применения конфига и изменения сбоя возвращают 409 с code=run_in_progress и details.run_id. Прогон ограничен по времени; при превышении лимита он завершается со статусом failed и снимает блокировку после прекращения нагрузки. Сбор оставшейся телеметрии не удерживает блокировку.
- После завершения нагрузки остановка возвращает 409 traces_pending с run_id, пока выборка этого стенда не зафиксирована. Ожидание ограничено сроком сбора. После ready или partial сервер завершает процессы через drain/flush и подтверждает освобождение слота.
- После перезапуска незавершённые прогоны получают failed; их request_id остаются связаны с исходным run_id. Сервер восстанавливает состояние супервизора, освобождает только подтверждённо принадлежащие стенду процессы и не принимает новую сборку до завершения очистки. Завершённые прогоны и подтверждённые выборки сохраняются.
- Проверка версий и начало прогона происходят под одной блокировкой стенда. Изменения конфига, сбоя и остановка используют ту же блокировку. Если сценарий, сбой или конфиг изменился, запуск отклоняется без HTTP-запросов. Пример ответа:

  ```json
  {"error":{"code":"stale_revision","message":"Конфигурация стенда изменилась","details":{"resource":"config","expected":"12","current":"13"},"retryable":false,"request_id":"req-42"}}
  ```

  Для scenario и fault поле resource меняется соответственно, а типы expected/current совпадают с типами версий запроса. Фронт перечитывает состояние, показывает новые значения и ждёт повторного действия пользователя. Для устаревшего плана применения создаётся новый предпросмотр с подтверждением.
- При запуске сервер атомарно привязывает пару stand_id и request_id к созданному run_id. Повтор с тем же телом возвращает тот же run_id и его актуальное состояние, даже если прогон завершился или стенд остановлен. Повтор с другим телом возвращает 409 request_id_conflict. Проверка уже принятого request_id предшествует проверке блокировки и ревизий. После потери ответа фронт повторяет тот же запрос с тем же ID.
- Если уже существует стенд, новая сборка отвечает 409 с code=stand_already_active и details.stand_id. Фронт открывает существующий стенд или предлагает остановить его перед сборкой нового.

### Готовность трасс к сравнению

- Пока выполняются запросы или сервер ожидает телеметрию, trace_collection.status равен waiting. Для первого среза expected_count равен числу отправленных входных запросов. received_count считает уникальные входные трассы с подходящим run_id, которые прошли проверку полноты для сравнения.
- После завершения нагрузки ждём до 20 секунд. Если все ожидаемые трассы готовы, фиксируем их ссылки на точные ревизии и выставляем ready. Если срок истёк, фиксируем доступную выборку и выставляем partial. Трассы, поступившие позже, видны в мониторинге, а результат сравнения этой пары прогонов остаётся воспроизводимым.
- Для проверки рекомендации сравниваются разные прогоны одного стенда с одной ревизией сценария, одинаковой нагрузкой и одной ревизией сбоя. Снимки конфигов должны различаться ровно одним проверяемым полем. Несовпадение даёт 422 runs_not_comparable с причиной. Разницу трактуем как результат данного эксперимента.
- POST /api/v1/comparisons возвращает 409 traces_pending, пока хотя бы одна выборка в waiting. Для partial ответ показывает фактический и ожидаемый размер каждой выборки. Если хотя бы одна выборка пуста, возвращается 422 insufficient_traces. Failed-прогон доступен для просмотра и возвращает 422 run_failed при сравнении.

### Автоматическая сборка

При потере ответа на POST фронт читает GET /api/v1/lab/stands/active. Для сборки по трассе он открывает готовый стенд только при совпадении полного source TraceRef с выбранной трассой; checkout-сценарий отслеживается по scenario_id. Если тот же стенд ещё building или stopping, фронт показывает состояние и повторяет чтение. При null предлагает повторить сборку по явному действию пользователя. Если активен стенд для другой трассы, предлагает открыть его или остановить. Слот резервируется до запуска процессов и остаётся занятым до подтверждённой очистки. Фоновой очереди сборок нет.

- Checkout сценарий включает последовательные шаги и параллельные группы. Для последующего генератора по трассе первая линейная версия допустима; параллельная генерация является необязательным расширением. Внешний адресат без серверного спана допустим как последний шаг, если виден однозначный исходящий HTTP-вызов и его исход.
- Один шаблон тестового сервиса запускается для каждого узла. Локальные адреса назначает стенд; исходные URL не используются для отправки запросов. Для конечного звена берутся наблюдаемый HTTP-статус и длительность. Измеренный интервал включает накладные расходы, поэтому это приближение задержки. Неизвестные параметры помечаются как defaults и показываются до эксперимента.
- После запуска проводится проверочный HTTP-запрос. Его трасса помечается как пробная и не входит в сравнение. Сбой запуска возвращает ошибку и освобождает частично поднятые процессы. Последующие изменения внешней трассы не меняют готовый стенд.

### Сценарий проверки рекомендации

1. Во внешней цепочке orders вызывает payment. Управляющий адаптер зарегистрирован для orders. Внешний payment наблюдаем по исходящим вызовам.
2. Из полной трассы собираем стенд. На стенде задаём воспроизводимый сбой: payment быстро отвечает 503 на каждый вызов. На стороне orders включены повторы для 503, максимум 3 попытки, расчётный таймаут попытки 250 мс, фиксированная пауза 50 мс, бюджет 700 мс. Для каждой попытки стенд делает отдельный вызов requests с отключёнными скрытыми повторами и перенаправлениями. Все попытки принадлежат одной входной трассе. На стенде входной спан содержит run_id, а клиентские спаны содержат стабильный logical_call_id внутри трассы и attempt_index от 1.
3. Делаем 30 последовательных запросов. R4 вычисляет расчётный бюджет 3 × 250 + 2 × 50 = 850 мс. Это модель по конфигурации. Параметр timeout в requests ограничивает ожидание на этапах соединения и чтения, поэтому результат R4 не выдаётся за жёсткую границу фактической длительности HTTP-запроса. Рекомендация меняет только max_attempts с 3 на 2; граница становится 2 × 250 + 50 = 550 мс.
4. Применяем рекомендацию и подтверждаем ревизию конфига. Повторяем те же 30 запросов с тем же сбоем. Сравниваем попытки на входной запрос, длительности и ошибки. Проверяем, что для каждой полной входной трассы есть три отдельных клиентских спана до изменения и два после. Итоговый ответ остаётся 503. Фактические задержки показываем отдельно по трассам. Сравнение становится доступно после фиксации непустых выборок обоих прогонов. При partial UI показывает фактический и ожидаемый размер.
5. Отдельно проверяем отказ в записи у внешнего payment, успешное чтение и ручную запись поддерживаемого поля orders, отказ при применении рекомендации к внешнему orders, конфликт устаревшей ревизии, остановку сборки на неполной цепочке и раздельный учёт внешних и лабораторных трасс.
