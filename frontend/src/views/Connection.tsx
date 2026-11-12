import { usePersistentState } from '../state';
import { useEffect, useRef, useState } from 'react';
import type { Capabilities, Connection } from '../api/types';
import { createClient, normalizeBaseUrl } from '../api/client';
import { Badge, Button, Field, Message, Panel, PageHeader } from '../components/ui';

export function ConnectionView({ connection, capabilities, onChange }: { connection: Connection; capabilities: Capabilities | null; onChange: (connection: Connection) => void }) {
  const [url, setUrl] = usePersistentState('connection:url-draft', connection.baseUrl || location.origin);
  const [environmentId, setEnvironmentId] = usePersistentState('connection:environment-draft', connection.environmentId || 'shop');
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState('');
  const request = useRef<AbortController | null>(null);

  useEffect(() => () => request.current?.abort(), []);

  async function connect(event: React.FormEvent) {
    event.preventDefault();
    setError('');
    setTesting(true);
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    try {
      const baseUrl = normalizeBaseUrl(url);
      const next = { mode: 'live' as const, baseUrl, environmentId: environmentId.trim() || 'shop' };
      await createClient(next).getCapabilities(controller.signal);
      if (!controller.signal.aborted) {
        setUrl(baseUrl);
        onChange(next);
      }
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Ошибка подключения');
    } finally {
      if (!controller.signal.aborted) setTesting(false);
    }
  }

  return <>
    <PageHeader title="Настройки подключения" />
    <div className="connection-grid">
      <Panel title="Сервер ZeroTrace" meta={<Badge tone={connection.mode === 'live' ? 'success' : 'neutral'}>{connection.mode === 'live' ? 'Выбран' : 'Доступен'}</Badge>}>
        <form className="connection-form" onSubmit={connect}>
          <Field label="Адрес сервера">
            <input id="server-url" type="url" required value={url} onChange={event => setUrl(event.target.value)} placeholder="http://127.0.0.1:8000" />
          </Field>
          <Field label="Окружение">
            <input id="environment-id" required value={environmentId} onChange={event => setEnvironmentId(event.target.value)} placeholder="shop" />
          </Field>
          {error && <Message tone="danger">{error} Подключение не изменено.</Message>}
          <Button icon="plug" variant="primary" type="submit" loading={testing}>{testing ? 'Проверяем сервер…' : 'Проверить и подключить'}</Button>
        </form>
      </Panel>
      <Panel title="Демонстрационный набор" meta={<Badge tone={connection.mode === 'demo' ? 'success' : 'neutral'}>{connection.mode === 'demo' ? 'Выбран' : 'Доступен'}</Badge>}>
        <div className="connection-form">
          <p>Готовые примеры запросов, проблем и лабораторных экспериментов. Подключение к серверу не требуется.</p>
          <Button onClick={() => {
            request.current?.abort();
            setTesting(false);
            setError('');
            onChange({ mode: 'demo', baseUrl: connection.baseUrl });
          }} disabled={connection.mode === 'demo'}>Открыть демоданные</Button>
        </div>
      </Panel>
    </div>
    <Panel title="Текущее подключение">
      <dl className="connection-details">
        <div><dt>Режим</dt><dd>{connection.mode === 'demo' ? 'Демонстрационный набор' : 'Подключённый сервер'}</dd></div>
        <div><dt>Адрес сервера</dt><dd>{connection.mode === 'live' ? connection.baseUrl || location.origin : 'Не используется'}</dd></div>
        <div><dt>Изменение настроек</dt><dd>{connection.mode === 'demo' ? 'Локальные изменения демонстрационного примера' : capabilities ? capabilities.apply_enabled ? 'Разрешено' : 'Недоступно' : 'Неизвестно'}</dd></div>
        <div><dt>Окружение</dt><dd className="mono">{connection.mode === 'demo' ? 'demo-shop' : connection.environmentId || 'Не задано'}</dd></div>
      </dl>
    </Panel>
  </>;
}
