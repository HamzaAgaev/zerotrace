import { useEffect, useRef } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import type { Tone } from '../api/types';
import { Icon } from './ui';

export function MetricInfo({ label, children }: { label: string; children: ReactNode }) {
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    function closeOutside(event: PointerEvent) {
      if (!ref.current?.contains(event.target as Node)) ref.current?.removeAttribute('open');
    }
    function escape(event: KeyboardEvent) {
      if (event.key === 'Escape') ref.current?.removeAttribute('open');
    }
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('keydown', escape);
    };
  }, []);
  return <details ref={ref} className="metric-info">
    <summary aria-label={label} title={label}><Icon name="info" size={16}/></summary>
    <div>{children}</div>
  </details>;
}

export function Metrics({ items }: { items: { label: string; value: ReactNode; tone?: Tone; info?: string }[] }) {
  return <dl className="metric-grid" style={{ '--metric-count': items.length } as CSSProperties}>{items.map(({ label, value, tone, info }) =>
    <div className={`metric-tile metric-tile--${tone ?? 'neutral'}`} key={label}>
      <dt><span>{label}</span>{info && <MetricInfo label={`О показателе «${label}»`}>{info}</MetricInfo>}</dt><dd>{value}</dd>
    </div>
  )}</dl>;
}

export function PairBars({ label, labelContent = label, values, limit, scale, info, labels = ['До', 'После'] }: {
  label: string;
  labelContent?: ReactNode;
  values: readonly [{ amount: number; text: string }, { amount: number; text: string }];
  labels?: readonly [string, string];
  scale?: number;
  info?: string;
  limit?: { amount: number; text: string };
}) {
  const max = Math.max(1, scale ?? 0, ...values.map(v => v.amount), limit?.amount ?? 0);
  return <figure className="pair-chart">
    <figcaption>{labelContent}{limit && <small>{limit.text}</small>}{info && <MetricInfo label={`Что значит «${label}»`}>{info}</MetricInfo>}</figcaption>
    {values.map(({ amount, text }, index) => <div className="pair-row" key={index}>
      <span>{labels[index]}</span>
      <span className="pair-track" aria-hidden="true">
        <i style={{ width: `${amount / max * 100}%` }}/>
        {limit && <b style={{ left: `${limit.amount / max * 100}%` }}/>}
      </span>
      <strong>{text}</strong>
    </div>)}
  </figure>;
}
