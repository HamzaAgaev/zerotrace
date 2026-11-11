import { useEffect, useReducer, useState } from 'react';
import { labSourceTrace } from '../api/fixtures';
import {
  comparisonIssue, initialLab, labReducer, loadCount, observedPayment, paymentFault, retryBudget, retryDefaults,
} from '../api/labFixture';
import type { Run } from '../api/labFixture';
import type { Client, TraceRef } from '../api/types';
import { Badge, Button, EmptyState, Message, Panel } from '../components/ui';
import { Metrics, PairBars } from '../components/Metrics';
import { shortId, traceHref } from '../lib';
import { LiveLab } from './LiveLab';

export function useLabFixture() {
  const [state, dispatch] = useReducer(labReducer, initialLab);
  const { stand, runs } = state;
  const runningId = runs.find(run => run.status === 'running')?.id;
  const collectingId = runs.find(run => run.status === 'completed' && run.collection === 'waiting')?.id;

  useEffect(() => {
    if (!stand || stand.status === 'ready') return;
    const type = stand.status === 'building' ? 'ready' : 'stopped';
    const timer = window.setTimeout(() => dispatch({ type, id: stand.id }), 450);
    return () => window.clearTimeout(timer);
  }, [stand]);
  useEffect(() => {
    if (!runningId) return;
    const timer = window.setTimeout(() => dispatch({ type: 'loadComplete', id: runningId }), 900);
    return () => window.clearTimeout(timer);
  }, [runningId]);
  useEffect(() => {
    if (!collectingId) return;
    const timer = window.setTimeout(() => dispatch({ type: 'tracesReady', id: collectingId }), 1100);
    return () => window.clearTimeout(timer);
  }, [collectingId]);

  return {
    state, dispatch,
    build(source: TraceRef) {
      dispatch({ type: 'build', source, id: crypto.randomUUID() });
      location.hash = '/lab';
    },
  };
}

function RunComparison({ runs }: { runs: Run[] }) {
  const [baseline, setBaseline] = useState('');
  const [candidate, setCandidate] = useState('');
  const before = runs.find(run => run.id === baseline) ?? [...runs].reverse().find(run => run.maxAttempts === 3);
  const after = runs.find(run => run.id === candidate) ?? [...runs].reverse().find(run => run.maxAttempts === 2);
  const issue = before && after ? comparisonIssue(before, after) : 'Выполните прогоны до и после применения рекомендации.';
  return <Panel title="Сравнение прогонов">
    <div className="lab-body">
      <div className="lab-actions">
        <label>До изменения <select value={before?.id ?? ''} onChange={e => setBaseline(e.target.value)}>
          <option value="" disabled>Выберите прогон</option>
          {runs.map((run, i) => <option key={run.id} value={run.id}>Прогон {i + 1} ({run.maxAttempts} попытки)</option>)}
        </select></label>
        <label>После изменения <select value={after?.id ?? ''} onChange={e => setCandidate(e.target.value)}>
          <option value="" disabled>Выберите прогон</option>
          {runs.map((run, i) => <option key={run.id} value={run.id}>Прогон {i + 1} ({run.maxAttempts} попытки)</option>)}
        </select></label>
      </div>
      {issue && <p className="muted" role="status">{issue}</p>}
      {!issue && before && after && <>
        <ComparisonCharts before={before} after={after}/>
        <div className="comparison-samples">
          {[before, after].map((run, index) => <Badge key={run.id} tone={run.collection === 'partial' ? 'warning' : 'neutral'}>
            {index === 0 ? 'До' : 'После'}: {run.samples.length}/{run.expected} трасс
            {run.collection === 'partial' && ' (неполная выборка)'}
          </Badge>)}
        </div>
      </>}
    </div>
  </Panel>;
}

function ComparisonCharts({ before, after }: { before: Run; after: Run }) {
  function summarizeRun(run: Run) {
    const count = run.samples.length;
    const attempts = run.samples.reduce((sum, sample) => sum + sample.attemptStatuses.length, 0) / count;
    const durations = run.samples.map(sample => sample.durationMs);
    const min = Math.min(...durations);
    const max = Math.max(...durations);
    const errors = run.samples.filter(sample => sample.status === 503).length;
    return {
      attempts: { amount: attempts, text: attempts.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) },
      duration: { amount: max, text: `${min}–${max} мс` },
      errors: { amount: errors / count, text: (errors / count).toLocaleString('ru-RU', { style: 'percent', maximumFractionDigits: 1 }) },
    };
  }
  const a = summarizeRun(before);
  const b = summarizeRun(after);
  return <div className="comparison-charts">
    <PairBars label="Среднее число попыток" values={[a.attempts, b.attempts]}
      info="Учитывается первый вызов payment и все повторы. Среднее считается по зафиксированным трассам."/>
    <PairBars label="Диапазон длительностей" values={[a.duration, b.duration]}
      info="Диапазон длительностей входных запросов в тестовых трассах. Длина полосы показывает максимум."/>
    <PairBars label="Доля ответов HTTP 503" values={[a.errors, b.errors]}
      scale={1} info="Доля входных запросов с итоговым ответом 503 среди зафиксированных трасс."/>
  </div>;
}

export function Lab({ lab, demo, client }: { lab: ReturnType<typeof useLabFixture>; demo: boolean; client: Client }) {
  if (!demo) return <LiveLab client={client}/>;
  const { state: { stand, runs, message }, dispatch } = lab;
  const [plan, setPlan] = useState<{ standId: string; revision: string } | null>(null);
  const displayedStandId = stand?.id ?? runs.at(-1)?.standId;
  const standRuns = runs.filter(run => run.standId === displayedStandId);
  const running = standRuns.some(run => run.status === 'running');
  const collecting = standRuns.some(run => run.collection === 'waiting');
  const editable = stand?.status === 'ready' && !running;
  const currentPlan = plan?.standId === stand?.id ? plan : null;
  const hasBaseline = standRuns.some(run => run.maxAttempts === 3 && run.samples.length > 0);

  return <>
    <div className="page-title"><div>
      <h1>Лаборатория</h1>
    </div>{demo && <Badge tone="warning" dot>Тестовые данные</Badge>}</div>
    {!demo ? <Message>Лабораторный API пока не подключён. Сценарий доступен в <a href="#/sources">демонстрационном режиме</a>.</Message> : <>
      <Message tone="warning">Результаты демо хранятся до перезагрузки вкладки.</Message>
      {message && <Message>{message}</Message>}
      {!stand ? <EmptyState icon="traces" title="Нет активного стенда" description="Откройте трассу вызова payment из orders и выберите «Собрать стенд»."
        action={<a className="button button--primary" href={traceHref(labSourceTrace.trace_ref)}>Открыть трассу вызова payment из orders</a>}/> :
        <div className="lab-grid lab-controls">
          <Panel title="Стенд для вызова payment из orders" meta={<Badge tone={stand.status === 'ready' ? 'success' : 'warning'}>{
            { building: 'Сборка', ready: 'Готов', stopping: 'Остановка' }[stand.status]
          }</Badge>}>
            <div className="lab-body">
              {stand.status === 'ready' && <Metrics items={[
                { label: 'Попыток', value: stand.maxAttempts },
                { label: 'Таймаут', value: `${retryDefaults.timeoutMs} мс` },
                { label: 'Пауза', value: `${retryDefaults.backoffMs} мс` },
                { label: 'Бюджет', value: `${retryDefaults.budgetMs} мс` },
              ]}/>}
              <details className="compact-details">
                <summary>Источник и параметры стенда</summary>
                <p><a className="mono" href={traceHref(stand.source)}>{shortId(stand.source.trace_id)}</a>
                  {', '}{stand.source.environment_id}, поколение {stand.source.generation}, ревизия {stand.source.revision}</p>
                {stand.status === 'ready' && <>
                  <p>POST /charge. В исходной трассе HTTP {observedPayment.httpStatus}, {observedPayment.durationMs} мс. Параметры повторов заданы демостендом.</p>
                  <p>Ревизии: сценарий {stand.scenarioRevision}, сбой {stand.faultRevision}, конфиг {stand.configRevision}</p>
                </>}
              </details>
              <Button disabled={!editable || collecting} onClick={() => dispatch({ type: 'stop' })}>Остановить</Button>
              {collecting && !running && <p className="muted">Остановка доступна после фиксации трасс.</p>}
            </div>
          </Panel>
          <Panel title="Сбой и нагрузка">
            <div className="lab-body">
              <Metrics items={[
                { label: 'Сбой payment', value: stand.fault ? `HTTP ${stand.fault.httpStatus}` : 'Выключен', tone: stand.fault ? 'danger' : 'neutral' },
                { label: 'Запросов подряд', value: loadCount },
                { label: 'Задержка сбоя', value: stand.fault ? `${stand.fault.delayMs} мс` : '—' },
              ]}/>
              <div className="lab-actions">
                <Button disabled={!editable} onClick={() => dispatch({ type: 'fault', enabled: !stand.fault })}>
                  {stand.fault ? 'Сбросить сбой' : `Задать HTTP ${paymentFault.httpStatus}`}
                </Button>
                <Button variant="primary" disabled={!editable || !stand.fault} onClick={() => dispatch({
                  type: 'run', id: crypto.randomUUID(),
                  versions: { scenarioRevision: stand.scenarioRevision, faultRevision: stand.faultRevision, configRevision: stand.configRevision },
                })}>Запустить запросы</Button>
              </div>
            </div>
          </Panel>
          {hasBaseline && <Panel title="Рекомендация R4" className="lab-wide">
            <div className="lab-body">
              <div className="comparison-charts">
                <PairBars label="Расчётный бюджет R4"
                  info="Число попыток × таймаут + паузы между попытками. Это расчёт по конфигу. Длительность запросов показана в сравнении прогонов." values={[
                  { amount: retryBudget(3) ?? 0, text: `${retryBudget(3)} мс` },
                  { amount: retryBudget(2) ?? 0, text: `${retryBudget(2)} мс` },
                ]} limit={{ amount: retryDefaults.budgetMs, text: `Лимит ${retryDefaults.budgetMs} мс` }}/>
              </div>
              {stand.maxAttempts === 2 ? <Badge tone="success">Применена (ревизия {stand.configRevision})</Badge> :
                currentPlan ? <>
                  <Metrics items={[
                    { label: 'Попыток до изменения', value: 3 },
                    { label: 'Попыток после изменения', value: 2 },
                    { label: 'Ревизия конфига', value: currentPlan.revision },
                  ]}/>
                  <details className="compact-details"><summary>Что изменится</summary>
                    <p>Поле <code>max_attempts</code> для POST /charge. При временном отказе будет на одну попытку меньше.</p>
                  </details>
                  <div className="lab-actions">
                    <Button variant="primary" disabled={!editable} onClick={() => {
                      dispatch({ type: 'apply', ...currentPlan });
                      setPlan(null);
                    }}>Применить рекомендацию</Button>
                    <Button onClick={() => setPlan(null)}>Закрыть план</Button>
                  </div>
                </> : <Button disabled={!editable} onClick={() => setPlan({ standId: stand.id, revision: stand.configRevision })}>Посмотреть рекомендацию</Button>}
            </div>
          </Panel>}
        </div>}
      {standRuns.length > 0 && <div className="lab-grid">
        <Panel title="Прогоны"><ul className="lab-runs" aria-live="polite">{standRuns.map((run, i) => <li key={run.id}>
          <strong>Прогон {i + 1} (конфиг {run.configRevision})</strong>
          <span>{run.status === 'running' ? 'Выполняются запросы' : run.status === 'failed' ? 'Прогон завершился с ошибкой' :
            run.collection === 'waiting' ? `Запросы ${run.completed}/${run.expected}, ждём трассы` :
              `Зафиксировано трасс: ${run.samples.length}/${run.expected}`}</span>
        </li>)}</ul></Panel>
        <RunComparison key={displayedStandId} runs={standRuns}/>
      </div>}
    </>}
  </>;
}
