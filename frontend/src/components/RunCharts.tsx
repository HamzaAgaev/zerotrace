import { LatencyHistogram } from './StatisticsCharts';
import { Panel } from './ui';
import type { runStatistics } from '../runStatistics';
function responseColor(label:string) {
  const status=Number(/^HTTP (\d{3})$/.exec(label)?.[1]);
  if(status>=200&&status<400)return 'var(--green)';
  if(status>=400||['Время ожидания истекло','Ошибка соединения','Ошибка запроса'].includes(label))return 'var(--red)';
  return 'var(--muted)';
}
export function RunCharts({stats}:{stats:ReturnType<typeof runStatistics>}) {
  let offset=0;
  const total=stats.count;
  const summary=!total?'Запросы ещё не выполнены.':stats.success===total
    ? `Все запросы завершились успешно (${total}).`
    : `Успешно: ${stats.success}. С ошибкой: ${stats.errors}.${stats.unknown?` Результат неизвестен: ${stats.unknown}.`:''}`;
  return <div className="test-chart-grid">
    <Panel title="Результаты запросов"><div className="lab-body">
      <div className="response-chart">
        <svg viewBox="0 0 160 160" role="img" aria-label={`Распределение результатов ${total} запросов`}>
          {stats.responses.map(([label,count])=>{const share=total?count/total*100:0;const start=offset;offset+=share;return <circle key={label} cx="80" cy="80" r="60" fill="none" stroke={responseColor(label)} strokeWidth="24" pathLength="100" strokeDasharray={`${share} ${100-share}`} strokeDashoffset={-start} transform="rotate(-90 80 80)"><title>{label}: {count} ({Math.round(share)}%)</title></circle>;})}
          <text x="80" y="78" textAnchor="middle" className="chart-total">{total}</text><text x="80" y="96" textAnchor="middle" className="chart-caption">запросов</text>
        </svg>
        <ul className="chart-legend">{stats.responses.map(([label,count])=><li key={label}><span className="chart-swatch" style={{background:responseColor(label)}}/><span>{label}</span><strong>{count} ({Math.round(total?count/total*100:0)}%)</strong></li>)}</ul>
      </div>
      <p className="response-chart-summary">{summary}</p>
    </div></Panel>
    <Panel title="Время запросов"><div className="lab-body"><LatencyHistogram buckets={stats.durations}/></div></Panel>
  </div>;
}
