import type { ReactNode } from 'react';
import type { Tone } from '../api/types';
import { Icon } from './ui';

export function MetricInfo({ label, children }: { label: string; children: ReactNode }) {
  return <details className="metric-info">
    <summary aria-label={label} title={label}><Icon name="info" size={16}/></summary>
    <div>{children}</div>
  </details>;
}

export function Metrics({ items }: { items: { label: string; value: ReactNode; tone?: Tone }[] }) {
  return <dl className="metric-grid">{items.map(({ label, value, tone }) =>
    <div className={`metric-tile metric-tile--${tone ?? 'neutral'}`} key={label}>
      <dt>{label}</dt><dd>{value}</dd>
    </div>
  )}</dl>;
}

export function PairBars({ label, values, limit, scale, info }: {
  label: string;
  values: readonly [{ amount: number; text: string }, { amount: number; text: string }];
  scale?: number;
  info?: string;
  limit?: { amount: number; text: string };
}) {
  const max = Math.max(1, scale ?? 0, ...values.map(v => v.amount), limit?.amount ?? 0);
  return <figure className="pair-chart">
    <figcaption>{label}{limit && <small>{limit.text}</small>}{info && <MetricInfo label={`Что значит «${label}»`}>{info}</MetricInfo>}</figcaption>
    {values.map(({ amount, text }, index) => <div className="pair-row" key={index}>
      <span>{index === 0 ? 'До' : 'После'}</span>
      <span className="pair-track" aria-hidden="true">
        <i style={{ width: `${amount / max * 100}%` }}/>
        {limit && <b style={{ left: `${limit.amount / max * 100}%` }}/>}
      </span>
      <strong>{text}</strong>
    </div>)}
  </figure>;
}
