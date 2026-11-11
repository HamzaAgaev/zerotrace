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
