import { usePersistentState, usePersistentTab } from '../state';
import { SpanOperation, endpointLabel } from './HttpText';
import { PairBars } from './Metrics';
import { Select } from './Select';
import { useMemo } from 'react';
import type { Span, TraceDetail } from '../api/types';
import { downloadJson, projectIntervals } from '../analysis';
import { Badge, Button, Message, Panel, TableScroll } from './ui';
import { Tabs } from './Tabs';
import { duration } from '../lib';
import { formatMs, formatPercent } from '../presentation';

const maxBranches = 8;

/** Full operation plus span-specific context so repeats of the same endpoint stay distinguishable. */
function spanOptionLabel(span: Span) {
  return `${endpointLabel(span.service, span.name)} · смещение ${duration(span.start_offset_ns)} от начала трейса · длительность ${duration(span.duration_ns)}`;
}

export function WhatIf({ trace }: { trace: TraceDetail }) {
  const traceKey = JSON.stringify(trace.trace_ref);
  const [tab, setTab] = usePersistentTab(`projection:${traceKey}:tab`, 'scenario', ['scenario', 'candidates', 'about'], 'projection');
  const [branches, setBranches] = usePersistentState<Record<string, number>>(`projection:${traceKey}:branches`, {});
  const [pick, setPick] = usePersistentState(`projection:${traceKey}:pick`, trace.spans.find(span => span.parent_span_id)?.span_id ?? '');
  const result = useMemo(() => {
    try {
      return { value: projectIntervals(trace, branches), error: '' };
    } catch (error) {
      return { value: null, error: error instanceof Error ? error.message : 'Модель недоступна' };
    }
  }, [trace, branches]);
  const ranking = useMemo(() => {
    try {
      projectIntervals(trace, {});
      return trace.spans.filter(span => span.parent_span_id).slice(0, 200)
        .map(span => ({ span, ...projectIntervals(trace, { [span.span_id]: 50 }) }))
        .sort((a, b) => b.gain - a.gain).slice(0, 5);
    } catch {
      return [];
    }
  }, [trace]);
  const conflicts = (id: string) => {
    const ancestors = (child: string, parent: string): boolean => {
      const seen = new Set<string>();
      let current = trace.spans.find(span => span.span_id === child);
      while (current?.parent_span_id && !seen.has(current.span_id)) {
        seen.add(current.span_id);
        if (current.parent_span_id === parent) return true;
        current = trace.spans.find(span => span.span_id === current!.parent_span_id);
      }
      return false;
    };
    return Object.keys(branches).some(other => other === id || ancestors(id, other) || ancestors(other, id));
  };
  const branchCount = Object.keys(branches).length;
  const atLimit = branchCount >= maxBranches;
  const pickConflict = !!pick && conflicts(pick);
  const add = (id: string) => {
    if (!atLimit && !conflicts(id)) {
      setBranches(old => ({ ...old, [id]: 50 }));
      setTab('scenario');
    }
  };

  return <div className="whatif-content">
    <Message>Это прогноз ускорения, а не измерение после изменения приложения.</Message>
    <Tabs label="Разделы моделирования" value={tab} onChange={setTab} items={[
      { value: 'scenario', label: 'Настройка' },
      { value: 'candidates', label: 'Что можно ускорить' },
      { value: 'about', label: 'Как считается' },
    ]} />

    <div hidden={tab !== 'scenario'}>
      <div className="policy-layout">
        <Panel title="Настройка ускорения">
          <div className="scenario-editor">
            <p className="muted">Можно добавить до 8 веток вызовов. Нельзя одновременно выбрать ветку и вложенный в неё вызов.</p>
            <div className="scenario-add">
              <Select aria-label="Ветка для ускорения" value={pick} onChange={setPick}>
                {trace.spans.filter(span => span.parent_span_id).map(span => <option
                  disabled={conflicts(span.span_id)}
                  key={span.span_id}
                  value={span.span_id}
                  label={spanOptionLabel(span)}>
                  {spanOptionLabel(span)}
                </option>)}
              </Select>
              <Button disabled={!pick || atLimit || pickConflict} onClick={() => add(pick)}>Добавить</Button>
            </div>
            {atLimit && <p className="muted">Достигнут предел: добавлено 8 веток. Удалите одну из выбранных веток, чтобы добавить новую.</p>}
            {!atLimit && pickConflict && <p className="muted">Вызов уже выбран или входит в выбранную ветку. Удалите пересекающуюся ветку, чтобы добавить его.</p>}
            {!atLimit && !pickConflict && trace.spans.filter(span => span.parent_span_id).length === 0 && <p className="muted">В этом трейсе нет дочерних вызовов, поэтому ускорять нечего.</p>}
            {Object.entries(branches).map(([id, percent]) => {
              const span = trace.spans.find(item => item.span_id === id);
              return <div className="scenario-branch" key={id}>
                <div>
                  <strong>{span?.service ?? 'Вызов недоступен'}</strong>
                  <span>{span ? <SpanOperation span={span} /> : 'Этот спан больше не найден в трейсе. Удалите ветку.'}</span>
                  {span && <small className="field-note">Смещение {duration(span.start_offset_ns)} от начала трейса · длительность {duration(span.duration_ns)}</small>}
                  <Button variant="ghost" aria-label={`Убрать ветку ${span ? endpointLabel(span.service, span.name) : id}`} icon="close"
                    onClick={() => setBranches(old => { const next = { ...old }; delete next[id]; return next; })} />
                </div>
                <label>Сократить длительность на <strong>{percent}%</strong>
                  <input type="range" min="0" max="100" step="5" value={percent}
                    onChange={event => setBranches(old => ({ ...old, [id]: Number(event.target.value) }))} />
                </label>
              </div>;
            })}
            {!branchCount && <p className="muted">Добавьте вызов или откройте вкладку «Что можно ускорить».</p>}
            <Button onClick={() => setBranches({})}>Сбросить настройку</Button>
          </div>
        </Panel>
        <Panel title="Прогноз длительности запроса">
          <div className="budget-preview">
            {result.value ? <>
              <Badge tone="accent">Прогноз, не измерение</Badge>
              <PairBars label="Длительность запроса" labels={['Факт', 'Прогноз']} values={[
                { amount: result.value.baseline, text: formatMs(result.value.baseline) },
                { amount: result.value.projected, text: formatMs(result.value.projected) },
              ]} />
              <p>Возможное сокращение: <strong>{formatMs(result.value.gain)}</strong> ({formatPercent(result.value.baseline ? result.value.gain / result.value.baseline : 0)})</p>
              <Button icon="download" onClick={() => downloadJson('zerotrace-scenario.json', {
                kind: 'interval-projection', model: 'browser-interval-blocks-preview-v1', trace_ref: trace.trace_ref,
                branches, result: result.value, measured: false, applied: false,
              })}>Скачать прогноз</Button>
            </> : <Message tone="warning">{result.error}</Message>}
            <p className="muted">Модель не учитывает изменение повторов и нагрузки. Ограничения описаны на вкладке «Как считается».</p>
            {result.error && <a className="button" href="#/traces">Выбрать другой трейс</a>}
          </div>
        </Panel>
      </div>
    </div>

    {tab === 'candidates' && (ranking.length > 0
      ? <Panel title="Что можно ускорить: сокращение длительности на 50%" meta={<Badge>Первые 200 спанов</Badge>}>
        <TableScroll>
          <table className="explore-table">
            <thead><tr><th>Сервис / операция</th><th>Прогноз длительности</th><th>Возможное сокращение</th><th>Действие</th></tr></thead>
            <tbody>{ranking.map(item => <tr key={item.span.span_id}>
              <td>{item.span.service}<small className="cell-note"><SpanOperation span={item.span} /></small>
                <small className="cell-note">Смещение {duration(item.span.start_offset_ns)} от начала · длительность {duration(item.span.duration_ns)}</small>
              </td>
              <td>{formatMs(item.projected)}</td>
              <td>{formatMs(item.gain)}</td>
              <td><Button disabled={atLimit || conflicts(item.span.span_id)} onClick={() => add(item.span.span_id)}>Добавить ветку</Button></td>
            </tr>)}</tbody>
          </table>
        </TableScroll>
        {atLimit && <p className="panel-note">Достигнут предел в 8 веток. Удалите выбранную ветку на вкладке «Настройка».</p>}
      </Panel>
      : <Message tone="warning">Варианты ускорения недоступны: {result.error || 'в трейсе нет дочерних веток'}.</Message>)}

    {tab === 'about' && <Panel title="Как считается прогноз">
      <div className="info-block">
        <p>Ускоряется вся выбранная ветка. Последовательные блоки сдвигаются; внутри перекрывающихся блоков сохраняются смещения запусков. Ненаблюдаемые зависимости, кэш и дедлайны не пересчитываются.</p>
        <p>Можно выбрать до восьми непересекающихся веток. Прогноз требует полного дерева с одним корнем и корректными границами спанов. Это предварительная модель интервалов, а не измерение после изменения приложения.</p>
      </div>
    </Panel>}
  </div>;
}
