# B — Backend 2: данные, хранение, Core и API данных

**Ответственный: Михаил Гончаров, MHS 1 курс, @mgoncharov. Роль: Backend Data/Core.**


Версия 0.4.1-draft. Полная роль. Реализация после согласования. Владеешь P1–P3 и общей моделью для аудита, what-if и сравнения; не реализуешь правила или UI. Ты один из трёх бэкендеров; frontend полностью у D.


## Первый показ с двумя режимами

Один приём OTLP для внешней системы и стенда. Изолировать данные по окружению, используя серверную привязку источника. Сохранять связь трасс стенда с `run_id` для выборок до и после. Трассы, сервисы и агрегаты фильтровать по выбранному окружению. Для генератора отдавать выбранную ревизию графа и признаки неполной цепочки.

## Задачи Михаила к первому показу

- [ ] Зафиксировать с Никитой идентификаторы окружения, источника, сервиса и прогона. Разделять внешний и лабораторный OTLP по серверной привязке источника.
- [ ] Принимать реальные OTLP-трассы, сохранять в SQLite и подтверждать приём после записи. Проверить повторную доставку, поздние спаны и чтение после перезапуска.
- [ ] Построить дерево запроса и общий граф сервисов, включая внешний callee, который виден только по исходящему спану. Отмечать неполные и неоднозначные связи.
- [ ] Дать Хамзе список и детали трасс. Для Никиты извлекать проверенную цепочку по точной ревизии или возвращать причину отказа. Для Амира фиксировать выборки и наблюдаемые вызовы по run_id. Проверить всё на записанных или тестовых OTLP-пакетах до подключения стенда.
- [ ] Сверить реализацию и примеры с [рабочим срезом P11](CONTRACTS.md#p11-согласованный-рабочий-срез-для-первого-показа).

**Готовность.** Оба внешних сервиса видны в одной цепочке, а лабораторные трассы читаются отдельно и выбираются по прогону.

## B1. Модули и API

`contracts/`, `ingest/`, `repository/`, `core/`, `app/data_api/`. HTTP wrappers данных принадлежат B; общий server/router wiring — A. Decoder/нормализация — B. D только вызывает API. Чистые преобразования не читают сеть/глобальное состояние. Repository — явно stateful слой приложения.

```text
decode_otlp(body, media_type, encoding, limits) → DecodedBatch
normalize(batch, source_profile, redaction_profile) → NormalizedBatch
repository.ingest(batch) → IngestResult с TraceRefs
repository.get_trace(TraceRef) → TraceGraph / unavailable
build_graph(normalized_spans, trace_ref) → TraceGraph
derive_calls(graph, grouping_policy) → CallModel
derive_timings(graph, calls, timing_policy) → TimingSamples
build_model(contributions, config, thresholds, known_edges) → ModelView
```

Входы/выходы описаны P1–P3. Algorithm/normalization/grouping versions явны. D и C не должны повторять разбор OTLP и heuristics callee у себя.

## B2. Приём и нормализация

Первый профиль — официальный OTLP protobuf decoder, затем JSON/JSONL/gzip и adapters полногоP1. Проверить ID/times/types/body limits до записи. JSON ns точен, hex/base64 различаются по объявленному формату/валидности. Legacy wrappers не маскируются под обязательный стандарт.

Сохранять объединённый набор полей обоих проектов: identity/version/env/instance, kind/parent, HTTP operation/status/target, error.type, resend, доступные DB/gRPC/idempotency признаки, links/async markers. Старый Go allowlist недостаточен для retry-аудита; старый Rust Span терял links, необходимые what-if. Unknown и coverage обязательны.

CLIENT/SERVER status раздельно. Поздний SERVER502 не становится CLIENT502. Ошибка без кода остаётся без кода. Короткий HTTP400 не fast_fail только из-за длительности. Вывод timeout из error.type/grpc deadline отличается от кластерной оценки; отсутствие ERROR не универсальный ok.

Пути нормализовать через route/template, иначе очищенный path с идентификаторами→placeholder и provenance. Не хранить query/тела/secrets. Адреса из spans не используются как control/Tempo URL.

## B3. Repository и доставка

SQLite transaction сохраняет пакет целиком и только затем ACK. unique(project,trace,generation,span). Дубликат не меняет число операций, revision и TTL. Поздний span дополняет trace и повышает revision; immutable conflict остаётся diagnostic с first-accepted содержимым.

TTL с first_received; last_received не продлевает возраст. Истёкший повторный импорт — новое generation; старые refs не переадресуются. Write failure откатывает ingest и pruning. Входящий пакет нельзя ACK и затем выбросить ради бюджета при той же транзакции.

Полная политика: возраст и размер, удаление старейших целых traces с deterministic tie; отдельный RAM cap; учёт WAL/SHM/temporary reserve. Из API expired trace скрывается сразу, cleanup bounded. User delete очищает cache/active contribution и показывает unavailable exemplars. Предлагаемые defaults 72 ч / 500 МиБ согласовать, не считать пользовательским решением.

Первый срез допускает синхронный полный пересчёт bounded dataset. Полная система: transaction trace+outbox event, идемпотентный worker, versioned cache, replacement contribution. Не внедрять сложность workers до потребности, но не нарушать гарантию при её появлении.

Интерактивное чтение использует отдельный согласованный снимок SQLite WAL. Приём спанов может продолжаться, пока API работает с зафиксированными ревизиями. Подсчёт спанов в списке ограничивается выбранным окружением. Время последней телеметрии и перевод тихих трейсов обслуживаются индексами. В P7 предусмотреть чтение телеметрии без аудита для первого отображения списка, карточки и выборки графа.

## B4. Граф и качество

Построить индекс span_id→node, parent/children, roots/components. Сортировка children=(start,end,id). Проверять циклы во всех компонентах до рекурсии, ограничить depth256. Несколько SERVER-детей CLIENT — неоднозначная пара, не «первый попавшийся».

Отсутствующий parent, неизвестный root, source loss и black-box server — разные факты. Quiet5с не полная трасса; forced close/лимиты отражаются. Сохранять исходные clocks; same service.name не same clock. Quality описывает факты, каждый алгоритм сам решает пригодность.

Граф сохраняется на диске: не выбрасывать после Attempt. What-if нужны INTERNAL-ветки/links/интервалы; audit нужна плоская модель с источниками. Ограниченный RAM-кэш выбранных traces вместо загрузки всей истории.

## B5. Attempts и logical calls

1. CLIENT синхронного профиля →Attempt; messaging не входит в sync CallModel.
2. Callee из достоверной SERVER-связи; иначе DB/peer/address с source, при отсутствии unknown.
3. Caller operation — ближайший пригодный SERVER того же instance/clock scope, иначе явно fallback.
4. Upstream relation по реальной ancestry; никакой цепочки только по совпавшим service names разных traces.
5. Группировка по owner/edge и подтверждённому профилю: resend/wrapper сильнее ошибки→следующего вызова. Overlap отдельно от доказанного hedge; mixed формы могут быть ambiguous.
6. Gaps только для последовательных retries; first_path nullable при неопределённой группе. Call IDs стабильны только внутри revision.

Пять успешных loop-вызовов — пять single. Ошибка→успех — гипотеза retry при отсутствии явного сигнала, не установленное бизнес-намерение. Это отличие от смелых heuristics прототипа, которое фиксируется в version.

## B6. Статистика, время, политики

EdgeStats/Policy по P3. Configured,declared,inferred хранятся отдельно, effective имеет strategy/source/freshness. Successful durations не доказательство настроенного timeout. При достаточных timeout/error samples допустимы annotated/cluster estimates с sample count/confidence и порогами.

Timing через union: parent1200ms, два child1000ms полностью перекрыты→uncovered200ms. Два определения — direct children и outgoing logical calls (с retry gaps); не смешивать. CLIENT-only время отражает вызывающего, неserver CPU. Нужны p50/p95/p99, не терять p95 для budget reserve.

Общие percentile/count/ratio denominators едины с cohorts. Idle edges из конфигов/known_edges остаются на графе, но no samples→null, неhealthy0%. Amplification calls-per-trace и retry amplification разные показатели.

## B7. Окно и поздние версии

Membership по first_received, точное[now−window,now), не минутное округление. Trace contribution keyed(project,trace,generation); новая revision заменяет old, включая сменившийся edge_id. После apply архив не очищается. До/после оцениваются отдельными epoch/dataset refs.

Устаревший worker result не заменяет новый. Cache key включает input+normalization/grouping/timing/config versions. Trace expiry/ref missing возвращается явно; comparison не выбрасывает такой элемент молча.

Список поддерживает серверные фильтры `span_id`, `start_from_ns` и `start_to_ns` по [контракту](CONTRACTS.md). Поиск по Span ID ограничивается окружением и текущей ревизией. Span ID ищется по индексу SQLite, а период проверяется по сводкам текущих ревизий. Приёмка включает попадание на позднюю страницу, замену ревизии поздним спаном, изоляцию окружений и обе границы периода.

## Этапы

| ID | Этап | Результат |
|---|---|---|
| B01 | M0 | P1–P3 типы и fixtures, валидатор |
| B02 | M1, день 1 | Protobuf→SQLite→graph→basic attempts |
| B03 | M1, день 2–3 | Duplicate/late/rollback/limits/restart |
| B04 | M2 | Полные calls/ambiguity/ancestry/timings/policies |
| B05 | M3 | Incremental window/outbox/cache/TTL/budget/search indexes |
| B06 | M4 | Полный import/export/Tempo adapters и immutable graph inputs P9/P10 |
| B07 | M5 | Profile/throughput/RSS/disk/performance |

## Тесты и готовность

Protobuf↔JSON normalization; ns roundtrip; invalid IDs/time; late parent; duplicate/conflict; cycle/depth; multiple server children; shifted clocks; late status; loop/retry/wrapper/fanout/hedge; unknown attrs; union overlap; idempotent contribution; crash/outbox replay; expired generation; write failure/disk budget.

Целевые performance-бюджеты согласуются и измеряются: batch 1 млн spans / 30 с / 2 ГиБ — старый ориентир, не установленная характеристика нового Python backend; interactive trace ≤2000 spans; live RAM bounded отдельно от disk. Готово, когда C/D могут воспроизвести результаты по contracts без чтения внутренних RawSpan/SQLite деталей.

## B8. Твои серверные API и переносимость

Владеешь POST /v1/traces, POST /api/v1/import, GET/DELETE traces, GET trace OTLP export, GET/PATCH services, GET storage, фильтрами/пагинацией. У каждого обработчика typed response по CONTRACTS. A подключает твой router к общему серверу; D получает примеры и OpenAPI.

A управляет удалёнными source adapters/Tempo HTTP-клиентом; после получения пакета всегда вызывает твой единый ingest. C получает immutable TraceRefs/graphs и storage interface для baseline/result persistence. A использует тот же repository interface для operation journal: не второй JSON-store.

Native Windows/macOS/Linux: pathlib и переносимые SQLite/Python primitives; без /proc, Unix sockets, обязательных bash/Make и предположения о fork. Вместе с A проверяешь start/import/save/restart/cleanup на CI всех трёх ОС. Системные счётчики RSS optional с unavailable, не обязательный Linux-пакет.

Дополнительные задачи: B08 (M1) data API/response fixtures; B09 (M3) полный search/cursor; B10 (M3–M5) миграции/TTL/диск/кроссплатформенные file locking и failure tests. Не перекладывать data handlers на фронтендера.
