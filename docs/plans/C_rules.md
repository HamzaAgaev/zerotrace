# C — Backend 3: аналитика, рекомендации и аналитические API

**Ответственный: Амир Гараев, MHS 1 курс, @garaevamir. Роль: Backend Analytics.**


Версия 0.4.1-draft. Полная роль, не только первый показ. Владеешь P5/P9/P10, thresholds и scorer P8. Правила и математические модели — чистые функции. Не владеешь hidden ground truth A и не вызываешь apply сетью. Помимо чистых библиотек пишешь свои серверные wrappers; D отвечает только за frontend.


## Первый показ с двумя режимами

Один анализатор для внешних и лабораторных трасс. Показывать наблюдаемое число попыток, интервалы и фактические таймауты при наличии сигналов. Настроенные значения указывать отдельно с источником. Рекомендацию можно применить на стенде, если его сервис-владелец поддерживает запись нужного поля. Для внешнего сервиса рекомендация информационная. Сравнение на стенде использует два реальных прогона.

## Задачи Амира к первому показу

- [ ] Согласовать с Мишей модель вызовов и признаки достаточности данных. С Никитой согласовать известные настройки и поддерживаемые цели изменения.
- [ ] Подготовить минимум одну диагностику по трассам. Кандидат — наблюдаемые повторы, если в телеметрии можно уверенно выделить попытки. Показать знаменатель и примеры.
- [ ] Реализовать R4 для известного конфига и выдать рекомендацию с расчётом, источником значений и указанием сервиса-владельца настройки.
- [ ] Дать Хамзе audit fixtures и API с положительным, здоровым и неопределённым результатами. На готовых примерах проверить сопоставимость и сравнение двух прогонов. При интеграции читать метаданные Никиты и зафиксированные выборки Миши.
- [ ] Сверить реализацию и примеры с [рабочим срезом P11](CONTRACTS.md#p11-согласованный-рабочий-срез-для-первого-показа).

**Готовность.** Пользователь видит, какие выводы получены из трасс, какие из конфига и как изменились наблюдаемые задержки, ошибки и повторы после эксперимента.

## C1. Библиотеки и интерфейсы

```text
rules.load_thresholds(config) → Thresholds
rules.run(model, policies, thresholds, baselines?) → AuditResult
projection.analyze(graph, ProjectionRequest, policy) → ProjectionResult
comparison.compare(graphs, CohortRequest, policy) → CohortResult
evalkit.score(actual_results, expected) → EvalResult
```

Чёткие версии ruleset/rule/projection/comparison; outputs несут input refs+hash и ограничения. D показывает результаты, не повторяет формулы в JS. B даёт graph/calls и общие union primitives, C определяет аналитику и gates.

## C2. Аудит

Аудит и индексация находок по TraceRef используют общий результат расчёта для одного набора точных ревизий, настроек и режима. Одновременные запросы этого набора делят один расчёт. Размер кэшей ограничен, изменение ревизий, удаление или новая конфигурация меняют ключ. Если актуальность baseline зависит от текущего времени, результат рассчитывается заново. Первый холодный расчёт проверяется отдельно от повторного.

Для браузера предусмотреть фоновый запуск и проверку готовности по идентификатору задачи. Ограничивать число активных и хранимых задач, проверять окружение, представление и режим при опросе. Синхронный API остаётся доступен CLI и существующим потребителям.

Полный реестр R1–R8/D1 и semantics — CONTRACTS§10. Первым R4, затем R1/R5 по пригодности источника; остальные обязательны для M2, а не вычеркнуты из системы.

Context строит индексы по trace,edge,call,upstream; правила не ходят по БД/сети. Каждый rule сообщает evaluated/insufficient/not_applicable/not_implemented и причины исключения. Empty findings не универсальное «здорово».

R1: не объявлять duration difference точным CPU/временем после disconnect. Наличие reference baseline позволяет поставить degraded как гипотезу, не доказать причину. R2 требует пригодного offset/ancestry. R3 использует observed paths, не соединяет произвольные service edges; independent fanout не retry. R5 CV — свойство наблюдения, не установленное отсутствие настройки jitter. R6 unknown idempotency не доказательство double charge. R7 только CLIENT-статус и protocol-specific policy. D1 проверяет freshness/versions/sampling.

Severity и sample thresholds централизованы. Доли только с явным eligible denominator. Популяционные выводы запрещены по tail-selected/unknown losses. Score задаёт приоритет отображения, не статистическую вероятность.

## C3. Рекомендации

Рекомендация = диагноз-гипотеза + арифметика + ожидаемое направление эффекта + риски + preconditions + typed intents. Primary/alternative/temporary различаются. Не обещать устранение причины только отменой лишней работы.

R4 need=N×timeout+Σpause. При3attempts base50exp Σpause=150ms. Full jitter upper bound отдельно от ожидаемой/наблюдаемой паузы. Cap, elapsed, reserve и unknown учитываются явно. Если healthy response или минимальный timeout не помещается, recommendation infeasible без применимого патча. Локальная пара рёбер не доказывает end-to-end SLA при sequential siblings.

Первый apply intent: один service/call, поддержанные timeout/attempts/backoff/jitter. Cancellation/deadline/idem позже только при capabilities. Intent не arbitrary patch, неадрес из span. A формирует конкретный config-version-bound plan и исполняет; D только показывает его и отправляет действие.

## C4. Baselines и provenance

Reference baseline сегментирован поedge/env/version/workload, содержит input refs, samples, sampling, age. Не обучается автоматически на selected-only данных или смешанных версиях. Порог ×1.5 за одно обновление сам по себе не спасает от постепенного дрейфа; reference обновляется явно с версией.

Configured/declared/inferred не сливаются. Observed max_attempts — нижняя наблюдаемая граница, не доказанная конфигурация. Timeouts по cluster — оценка с confidence и min samples. Priors/пороги старого стенда не становятся универсальным знанием о production.

## C5. Моделирование ускорений

Реализовать interval-blocks-v1 по P9: дерево прямых детей→overlap blocks→сохранённые gaps/offsets→рекурсивный пересчёт. Пользователь может выбрать root/subtree и до 32 веток. Выбранный предок включает потомков; nested selection исключается с notice.

Рейтинг — независимые оценки25/50/100%. Совместный результат вычисляется заново, не суммируется. Limiting ties сохраняются, no-effect поясняется. Observed timeline остаётся наблюдаемым, projected durations отдельно.

Гейты: cycles/depth, missing parents/root ambiguity, relevant links/async, несовместимые temporal bounds/clocks. Допуск500000ns версионирован; clipped timestamps только в модели. Непригодно→gain/projected=null, а не0. Явный subtree не делает всю исходную трассу полной.

Никаких реальных вызовов приложения, replay или изменения конфигов во время analyze. Shrink branch не симулирует изменение attempts/timeout/backoff; такой прогноз потребует отдельной модели, в этот алгоритм не подмешивается.

Контрольные числа: parallel1000/800→800 при первой−50%; serial→1300; обе parallel−50%→500; shifted start900+800 сохраняет 1700 при удалённой первой ветке; nested не вычитается дважды; tied limiters все показаны.

## C6. Сравнение групп

P10: две непересекающиеся группы1..100 refs; сопоставимость entry/env/workload, version как возможная исследуемая переменная. Missing/expired/stale input — явная ошибка, не тихое сокращение группы. Unknown workload — exploratory warning.

Root latency/ошибки отдельно от internal errors. Direct outgoing calls выбранной entry boundary, counts per trace, длительности, overlap=sum/union. Не смешивать вложенные уровни или разные denominators. p95 малой группы нестабилен, comparison описательный, не доказанная причинность.

Пример root200 после internal503+успешныйretry: вход успешен, internal_error присутствует. Serial/chatty profiles дают наблюдения о меньшем перекрытии/большем числе вызовов; business redundancy не утверждается без иных данных.

## C7. Eval

A создаёт scenarios/expected, C — scorer. Trace-only не видит runtime configs/chaos oracle; config-aware получает P4 явно. Обязательные outcomes и неожиданные findings размечаются/публикуются. Precision/recall только по размеченным; unlabelled_count рядом.

Проекция: exact synthetic oracle + независимые измерения A/reportlab. Показывать prediction MAE/relative error и availability/abstention отдельно от precision правил. Проверка latency модели не становится универсальным доказательством причинности.

## Этапы

| ID | Этап | Результат |
|---|---|---|
| C01 | M0 | Реестр/P5 fixtures/thresholds, ручной s01 |
| C02 | M1, день 1 | R4 и typed recommendation |
| C03 | M1, день 2–3 | R1/R5 по source profile, negative/infeasible tests |
| C04 | M2 | Все R1–R8/D1, ambiguity/gates, eval и baselines |
| C05 | M3 | Калибровка/window behaviour и verification semantics с A; D отображает статусы |
| C06 | M4 | P9 single/joint/subtree/ranking/limiting branches |
| C07 | M4 | P10 cohorts и descriptive diagnostics |
| C08 | M5 | Внешние traces, независимые measured validations |

## Тесты и готовность

На каждый rule: positive/negative/unknown, источники/единицы/evidence. R4 arithmetic fixed/exp/cap/jitter+infeasible; R1 hypotheses с/без baseline; R3 loop/fanout/real path; R6 unknown key. Projection gates/0..100/duplicates/nested/ties/stale refs/no-replay. Cohorts empty/overlap/mismatch/unknown/version/internal success и consistent percentile.

Property tests: immutable inputs; deterministic output; projected duration∈[0,baseline]; рост speedup не увеличивает модельное время; union неотрицателен/≤parent; permutation invariance неконфликтных spans. Готово, когда весь результат понятен по JSON/HTML без чтения кода C и без знания сценария неисправности.

## Контрольные проверки полной системы

| Набор | Положительное ожидание | Отказ/negative |
|---|---|---|
| R1 | Надёжная пара, server duration существенно больше ошибочного client | Нет server/качества → insufficient; нормальный ответ без finding |
| R2 | Поздний downstream с надёжной upstream-связью | Ранний старт/неизвестный clock offset не даёт уверенного finding |
| R3 | Реальная вложенная retry-цепочка усиливает attempts | Пагинация/бизнес-fanout не становится штормом |
| R4 | need выше известного budget, operands правильны | Unknown timeout молчит; невозможный budget → infeasible |
| R5 | Достаточно serial retry gaps без паузы/вариативности | Мало samples/ambiguous group не даёт уверенного вывода |
| R6/R7 | Риск неидемпотентного retry/клиент видел запрещённый код | Unknown header не доказанный дубль; SERVER status не CLIENT status |
| R8/D1 | Сопоставимые известные политики | Разные версии/старый конфиг/unknown ограничивают вывод |
| Projection | Точные serial/parallel/nested/joint/shifted/ties числа | Cycle/missing/links/async/bounds не дают числового обещания |
| Cohorts | Chatty/serial дают наблюдения counts/overlap | Overlap групп/mismatch/missing refs отвергаются; small sample помечен |

Пороговые решения фиксируются до eval, не подгоняются под случайный seed. В controlled lab публикуются все измерения, допуск, availability и prediction error; отсутствие warning не доказывает полноту.

## C8. Твои серверные API, Snapshot и отчёты

Владеешь GET audit, GET state, POST projections, POST comparisons, GET report/reports/{id}, orchestration алгоритмов и аналитическими CLI audit/project/compare/eval/export. Pure rules/projection/comparison остаются без сети; HTTP wrappers лежат отдельно в app/analytics_api/.

Строишь общий Snapshot/ReportBundle из refs B и journal summary A, сохраняешь версии/limitations и отдаёшь D готовые числовые результаты. Сборка JSON и автономного HTML-артефакта — C; D поставляет browser viewer/CSS/assets, C безопасно встраивает данные. Бизнес-арифметики в JS нет.

Хранение baseline/result metadata — через интерфейс B. A создаёт конкретный ChangePlan и выполняет control; C выдаёт Recommendation/Intent и чистую функцию оценки symptom/coverage. A владеет verification scheduler и journal API. Так вычисление и применение не дублируются.

Дополнительные задачи: C09 (M1) audit/state API+fixtures для D; C10 (M3) Snapshot/JSON/HTML builder+CLI; C11 (M4) projection/comparison wrappers и input ref validation. Pure analytics tests запускаются на всех трёх ОС; никакого обязательного Linux-specific dependency.
