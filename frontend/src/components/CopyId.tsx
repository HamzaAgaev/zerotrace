import { useEffect, useState } from 'react';
import { Button } from './ui';

export function CopyId({ label, value }: { label: 'Trace ID' | 'Span ID'; value: string }) {
  const [copiedAt, setCopiedAt] = useState<number>();
  const copied = copiedAt !== undefined;

  useEffect(() => {
    if (copiedAt === undefined) return;
    const timer = window.setTimeout(() => setCopiedAt(undefined), 1600);
    return () => window.clearTimeout(timer);
  }, [copiedAt]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopiedAt(Date.now());
    } catch {
      setCopiedAt(undefined);
    }
  }

  return <span className="trace-copy-id">
    <code className="mono">{value}</code>
    <Button type="button" className="trace-copy-button" icon={copied ? 'check' : 'copy'}
      aria-label={`Скопировать ${label}`} title={copied ? 'Скопировано' : `Скопировать ${label}`}
      onClick={() => void copy()}/>
    <span className="sr-only" role="status">{copied ? `${label} скопирован` : ''}</span>
  </span>;
}

