import { useEffect, useMemo, useState } from 'react';
import type { Client, TraceCohortComparison, TraceSummary } from '../api/types';
import { downloadJson } from '../analysis';
import { Badge, Button, LoadingState, Message, Panel, TableScroll } from '../components/ui';
import { formatCount, formatMs, formatPercent } from '../presentation';

export type ComparisonSelection = Record<string, { group: 'a' | 'b'; trace: TraceSummary }>;
const latency = (value: string | null) => value === null ? 'Неизвестно' : formatMs(Number(BigInt(value)) / 1e6);
const warning = (code: string) => ({
  small_sample_p95: 'Малая выборка: медиана и p95 могут быть нестабильны.',
  unknown_workload_id: 'Нагрузка для части запросов неизвестна.',
  sampled_or_unknown_input: 'Полнота исходной выборки не подтверждена.',
  trace_quality_issues: 'В исходных трейсах есть замечания к качеству данных.',
}[code] ?? code.replaceAll('_', ' '));

export function Compare({ client, selection }: { client: Client; selection: ComparisonSelection }) {
  const groups = useMemo(() => {
    const entries = Object.values(selection);
    return {
      a: entries.filter(item => item.group === 'a').map(item => item.trace.trace_ref),
      b: entries.filter(item => item.group === 'b').map(item => item.trace.trace_ref),
    };
  }, [selection]);
  const key = JSON.stringify(groups);
  const issue = !groups.a.length || !groups.b.length ? 'Выберите хотя бы один запрос в каждой группе.'
    : groups.a.length > 100 || groups.b.length > 100 ? 'В каждой группе может быть не более 100 запросов.' : '';
  const [state, setState] = useState<{ key: string; data: TraceCohortComparison | null; error: string; pending: boolean }>({ key: '', data: null, error: '', pending: true });
  useEffect(() => {
    const controller = new AbortController();
    setState({ key, data: null, error: '', pending: !issue });
    if (!issue) {
      void client.compareTraceGroups(groups.a, groups.b, controller.signal).then(data => {
        if (!controller.signal.aborted) setState({ key, data, error: '', pending: false });
      }).catch(cause => {
        if (!controller.signal.aborted) setState({ key, data: null, pending: false,
          error: cause instanceof Error ? cause.message : 'Не удалось сравнить выбранные группы.' });
      });
    }
    return () => controller.abort();
  }, [client, groups, key, issue]);

  if (issue) return <Message tone="warning">{issue}</Message>;
  if (state.key !== key || state.pending) return <LoadingState>Сравниваем выбранные группы…</LoadingState>;
  if (state.error) return <Message tone="warning">{state.error}</Message>;
  const result = state.data;
  if (!result || result.status !== 'available') return <Message tone="warning">Для этих групп сравнение недоступно.</Message>;
  const a = result.baseline, b = result.candidate;
  return <Panel title="Результат сравнения" meta={<Badge>{formatCount(a.count)} + {formatCount(b.count)} запросов</Badge>}>
    <div className="lab-body">
      <p>Сравнение двух групп одного эндпоинта. Различия сами по себе не доказывают эффект изменения настроек.</p>
      {result.warnings.map(code => <Message tone="warning" key={code}>{warning(code)}</Message>)}
      <TableScroll><table>
        <thead><tr><th>Показатель</th><th>Группа A</th><th>Группа B</th></tr></thead>
        <tbody>
          <tr><td>Количество запросов</td><td>{formatCount(a.count)}</td><td>{formatCount(b.count)}</td></tr>
          <tr><td>Медиана длительности</td><td>{latency(a.root_latency_p50_ns)}</td><td>{latency(b.root_latency_p50_ns)}</td></tr>
          <tr><td>95-й перцентиль</td><td>{latency(a.root_latency_p95_ns)}</td><td>{latency(b.root_latency_p95_ns)}</td></tr>
          <tr><td>Ответы с ошибкой</td><td>{formatCount(a.root_errors)} ({formatPercent(a.root_error_ratio)})</td><td>{formatCount(b.root_errors)} ({formatPercent(b.root_error_ratio)})</td></tr>
          <tr><td>Запросы с измеренной длительностью</td><td>{formatCount(a.coverage.root_latency_count)}</td><td>{formatCount(b.coverage.root_latency_count)}</td></tr>
        </tbody>
      </table></TableScroll>
      <p>Изменение медианы B − A: {latency(result.delta_ns)} ({formatPercent(result.delta_ratio)}).</p>
      <Button icon="download" onClick={() => downloadJson('zerotrace-comparison.json', result)}>Скачать JSON-отчёт</Button>
    </div>
  </Panel>;
}
