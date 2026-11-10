import type { ButtonHTMLAttributes, ReactNode } from 'react';
import type { Tone } from '../api/types';
export type IconName = 'graph' | 'clock' | 'compare' | 'traces' | 'audit' | 'journal' | 'plug' | 'search' | 'refresh' | 'chevron' | 'arrow' | 'download' | 'close' | 'check' | 'copy' | 'info' | 'external' | 'filter';
const paths: Record<IconName, ReactNode> = {
  graph: <><rect x="2" y="8" width="6" height="7" rx="1"/><rect x="16" y="2" width="6" height="7" rx="1"/><rect x="16" y="16" width="6" height="7" rx="1"/><path d="M8 11h4V5h4m-4 6v8h4"/></>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/></>,
  compare: <><path d="M4 5h16M4 12h9M4 19h16"/><path d="m16 9 4 3-4 3"/></>,
  traces: <><path d="M4 6h4m4 0h8M4 12h8m4 0h4M4 18h4m4 0h8"/><circle cx="10" cy="6" r="2"/><circle cx="14" cy="12" r="2"/><circle cx="10" cy="18" r="2"/></>,
  audit: <><path d="M12 3 4 6v5c0 5 8 10 8 10s8-5 8-10V6l-8-3Z"/><path d="M12 8v5m0 3h.01"/></>,
  journal: <><path d="M6 3h12v18H6zM9 7h6M9 11h6M9 15h4"/></>,
  plug: <><path d="M8 3v5m8-5v5M6 8h12v3a6 6 0 0 1-12 0V8Zm6 9v4"/></>,
  search: <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></>,
  refresh: <><path d="M20 8a8 8 0 1 0 .3 7M20 3v5h-5"/></>,
  chevron: <path d="m9 6 6 6-6 6"/>, arrow: <path d="M5 12h14m-5-5 5 5-5 5"/>,
  download: <><path d="M12 3v12m-5-5 5 5 5-5M5 16v5h14v-5"/></>,
  close: <path d="m6 6 12 12M6 18 18 6"/>, check: <path d="m5 12 4 4L19 6"/>,
  copy: <><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/></>,
  info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/></>,
  external: <><path d="M14 3h7v7m0-7-10 10M10 3H3v18h18v-7"/></>,
  filter: <path d="M4 5h16l-6 7v6l-4 2v-8L4 5Z"/>,
};
export function Icon({ name, size = 18, className = '' }: { name: IconName; size?: number; className?: string }) { return <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>; }
export function Button({ children, variant = 'secondary', icon, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost'; icon?: IconName }) { return <button className={`button button--${variant} ${className}`} {...props}>{icon && <Icon name={icon} size={16}/>} {children}</button>; }
export function Badge({ children, tone = 'neutral', dot = false }: { children: ReactNode; tone?: Tone; dot?: boolean }) { return <span className={`badge badge--${tone}`}>{dot && <i/>}{children}</span>; }
export function Panel({ title, meta, children, className = '' }: { title?: ReactNode; meta?: ReactNode; children: ReactNode; className?: string }) { return <section className={`panel ${className}`}>{title && <div className="panel-heading"><h2>{title}</h2>{meta}</div>}{children}</section>; }
export function EmptyState({ title, description, action, icon = 'search' }: { title: string; description: string; action?: ReactNode; icon?: IconName }) { return <div className="empty-state"><div className="empty-icon"><Icon name={icon} size={24}/></div><h2>{title}</h2><p>{description}</p>{action}</div>; }
export function Message({ children, tone = 'neutral', action }: { children: ReactNode; tone?: 'neutral' | 'warning' | 'danger'; action?: ReactNode }) { return <div className={`message message--${tone}`} role={tone === 'danger' ? 'alert' : 'status'}><Icon name="info" size={17}/><div>{children}</div>{action}</div>; }
