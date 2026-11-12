import { useEffect, useId, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import type { Tone } from '../api/types';
import { Icon } from './ui';

export function MetricInfo({ label, children }: { label: string; children: ReactNode }) {
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const close = () => ref.current?.hidePopover();
    window.addEventListener('resize', close);
    window.addEventListener('scroll', close, true);
    return () => { window.removeEventListener('resize', close); window.removeEventListener('scroll', close, true); };
  }, []);
  function toggle() {
    const popup = ref.current;
    if (!popup || !button.current) return;
    if (popup.matches(':popover-open')) { popup.hidePopover(); return; }
    popup.showPopover();
    const anchor = button.current.getBoundingClientRect();
    const box = popup.getBoundingClientRect();
    popup.style.left = `${Math.max(12, Math.min(anchor.left, window.innerWidth - box.width - 12))}px`;
    popup.style.top = `${Math.max(12, anchor.bottom + box.height + 8 < window.innerHeight ? anchor.bottom + 8 : anchor.top - box.height - 8)}px`;
  }
  return <span className="metric-info">
    <button ref={button} type="button" aria-label={label} aria-expanded={open} aria-controls={id} onClick={toggle}><Icon name="info" size={16}/></button>
    <div ref={ref} id={id} popover="auto" className="info-popover" onToggle={event=>setOpen(event.newState === 'open')}>{children}</div>
  </span>;
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
