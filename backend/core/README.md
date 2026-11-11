# core

**Зачем:** чистая аналитика над графом трасс — без сети, диска и
глобального состояния. Это вход для аудита Амира (C) и данных стенда
Никиты (A). Зона B (Миша).

**Модули:**

- `graph.py` — `build_graph(spans, trace_ref, ...)` → `TraceGraph`:
  индексы parent/children, корни, сортировка детей по (start, end, id);
  итеративная проверка циклов во всех компонентах; лимит глубины 256;
  quality-коды (missing_parent, multiple_roots, cycle, depth_limit,
  cross_clock_uncertain и др.).
- `calls.py` — `derive_calls(graph)` → `CallModel`: CLIENT-спаны →
  `Attempt` (attempt_id = span id; messaging исключается); callee из
  достоверной SERVER-связи, иначе target/db/unknown с источником;
  outcome по правилам (отсутствие ERROR не доказывает ok); группировка в
  `LogicalCall`: явные маркеры сильнее эвристики «ошибка → следующий
  вызов», overlap → ambiguous, пять успешных вызовов — пять single.
- `timing.py` — интервалы наносекунд: `union_duration`, два раздельных
  показателя uncovered (direct children ≠ outgoing calls), offsets только
  на совместимых часах, p50/p95/p99 через контрактный перцентиль.
- `chain.py` — `extract_chain(graph, calls)`: проверенная последовательная
  цепочка для генератора стенда (стык с A) либо типизированный отказ
  (ambiguous_root, missing_link, parallel_branches, cycle...) с
  конкретными проблемными связями.
- `model.py` — per-edge модель P3 (этап B04): `build_edge_stats`
  агрегирует `EdgeStats` из списка `EdgeContribution` (пары graph+calls по
  датасету трасс): attempts/eligible/ambiguous calls, outcomes, coverage,
  success p50/p95/p99, gaps_by_index, jitter_cv; ребро без попыток не
  создаётся (no samples → null, не ноль). `build_edge_policy` поднимает
  типизированный `EdgePolicy` из плоского снимка конфигурации A:
  ms → ns, `Sourced`-операнды с freshness/stale/binding_conflict,
  effective_source = fresh_runtime | declared | cautious_inference.
  `build_model(contributions, policies, known_edges)` → `ModelView`:
  статистика + политики по edge_id, idle рёбра из known_edges остаются со
  stats=None.

Все функции детерминированы: одинаковый вход (та же ревизия) → одинаковый
выход. `call_id` стабилен только внутри ревизии — устойчивые ссылки
делаются через TraceRef и исходные span ID.
