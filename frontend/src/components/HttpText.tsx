import { splitHttpOperation } from '../lib';
import type { Span } from '../api/types';

export const httpCodeLabel = 'Код HTTP';

export function HttpMethod({ method }: { method: string }) {
  return <span data-http-method={method}>{method}</span>;
}

export function HttpOperation({ operation }: { operation: string }) {
  const request = splitHttpOperation(operation);
  return request
    ? <><HttpMethod method={request.method}/>{' '}{request.endpoint}</>
    : operation;
}

type ServiceCall = { caller: string; callee: string; operation?: string };

function serviceCallTarget({ callee, operation }: ServiceCall) {
  const request = splitHttpOperation(operation ?? '', true);
  const endpoint = request?.endpoint ?? operation ?? '';
  const target = endpoint.startsWith('/') ? callee + endpoint : [callee, endpoint].filter(Boolean).join(' ');
  return { method: request?.method, target };
}

export function serviceCallLabel(call: ServiceCall) {
  const { method, target } = serviceCallTarget(call);
  return [call.caller, '→', method, target].filter(Boolean).join(' ');
}

export function ServiceCall({ call }: { call: ServiceCall }) {
  const { method, target } = serviceCallTarget(call);
  return <span className="service-call">{call.caller} <span className="muted">→</span>{' '}
    {method && <><HttpMethod method={method}/>{' '}</>}{target}
  </span>;
}

export function SpanOperation({ span }: { span: Pick<Span, 'name' | 'http_status'> }) {
  const observedHttp = span.http_status !== null;
  const request = splitHttpOperation(span.name, observedHttp);
  if (!request || (!observedHttp && !/^(?:\/|https?:\/\/)/i.test(request.endpoint))) {
    return span.name;
  }
  return <>
    <HttpMethod method={request.method}/>
    {request.endpoint && <> {request.endpoint}</>}
  </>;
}

export function HttpStatus({ code }: { code: number }) {
  return <span data-http-status={code}>{code}</span>;
}

export function HttpTarget({ target }: { target: string }) {
  const separator = ' / ';
  const index = target.indexOf(separator);
  if (index < 0) return <HttpOperation operation={target}/>;
  const start = index + separator.length;
  return <>{target.slice(0, start)}<HttpOperation operation={target.slice(start)}/></>;
}

/** Shared label for menus and accessible text. Unknown paths are never invented. */
export function endpointLabel(service: string, operation: string) {
  const request = splitHttpOperation(operation, true);
  const path = request?.endpoint ?? operation;
  const target = /^(\S+)\s+(\/.*)$/.exec(path);
  return [request?.method, target ? target[1]+target[2] : path.startsWith('/') ? service + path : `${service} · ${path || 'Путь не указан'}`].filter(Boolean).join(' ');
}
