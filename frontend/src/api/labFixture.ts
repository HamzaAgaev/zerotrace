import { checkBudget } from '../analysis.js';
import { sameTraceRef } from './client.js';
import { labSourceTrace } from './fixtures.js';
import type { TraceRef } from './types.js';

export const loadCount = 30;
export const retryDefaults = { maxAttempts: 3, timeoutMs: 250, backoffMs: 50, budgetMs: 700 };
export const paymentFault = { serviceId: 'payment', httpStatus: 503, delayMs: 0 } as const;
export const observedPayment = { httpStatus: 200, durationMs: 18 };

interface Versions {
  scenarioRevision: number;
  faultRevision: number;
  configRevision: string;
}

export interface Stand extends Versions {
  id: string;
  source: TraceRef;
  status: 'building' | 'ready' | 'stopping';
  fault: typeof paymentFault | null;
  maxAttempts: number;
}

export interface Run extends Versions {
  id: string;
  standId: string;
  status: 'running' | 'completed' | 'failed';
  collection: 'waiting' | 'ready' | 'partial';
  completed: number;
  expected: number;
  fault: typeof paymentFault;
  maxAttempts: number;
  samples: { attemptStatuses: number[]; status: number; durationMs: number }[];
}

export interface LabState {
  stand: Stand | null;
  runs: Run[];
  message: string;
}

export const initialLab: LabState = { stand: null, runs: [], message: '' };
export const supportsLabSource = (source: TraceRef) => sameTraceRef(source, labSourceTrace.trace_ref);
export function retryBudget(attempts: number) {
  return checkBudget({
    attempts, timeout: retryDefaults.timeoutMs, backoff: retryDefaults.backoffMs, budget: retryDefaults.budgetMs,
  }).required;
}

type Action =
  | { type: 'build'; source: TraceRef; id: string }
  | { type: 'ready' | 'stopped' | 'loadComplete' | 'tracesReady'; id: string }
  | { type: 'stop' }
  | { type: 'fault'; enabled: boolean }
  | { type: 'run'; id: string; versions: Versions }
  | { type: 'apply'; standId: string; revision: string };

export function labReducer(state: LabState, action: Action): LabState {
  const { stand, runs } = state;
  const reject = (message: string) => ({ ...state, message });
  if (action.type === 'build') {
    if (stand) return reject(sameTraceRef(stand.source, action.source) ? '' :
      'Уже есть стенд для другой трассы. Остановите его перед новой сборкой.');
    if (!supportsLabSource(action.source)) return reject('Для этой ревизии трассы нет примера сборки. Выберите трассу вызова payment из orders.');
    return { ...state, message: '', stand: {
      id: action.id, source: action.source, status: 'building', scenarioRevision: 1,
      faultRevision: 0, configRevision: '12', fault: null, maxAttempts: retryDefaults.maxAttempts,
    } };
  }
  if (action.type === 'loadComplete' || action.type === 'tracesReady') {
    return { ...state, runs: runs.map(run => {
      if (run.id !== action.id) return run;
      if (action.type === 'loadComplete' && run.status === 'running') {
        return { ...run, status: 'completed', completed: run.expected };
      }
      if (action.type === 'tracesReady' && run.status === 'completed' && run.collection === 'waiting') {
        const samples = Array.from({ length: run.expected }, (_, index) => ({
          attemptStatuses: Array<number>(run.maxAttempts).fill(run.fault.httpStatus),
          status: run.fault.httpStatus,
          durationMs: 4 + run.maxAttempts * 8 + (run.maxAttempts - 1) * retryDefaults.backoffMs + index % 5,
        }));
        return { ...run, collection: 'ready', samples };
      }
      return run;
    }) };
  }
  if (!stand) return state;
  if (action.type === 'ready') {
    return stand.id === action.id && stand.status === 'building'
      ? { ...state, stand: { ...stand, status: 'ready' } } : state;
  }
  if (action.type === 'stopped') {
    return stand.id === action.id && stand.status === 'stopping' ? { ...state, stand: null } : state;
  }
  if (stand.status !== 'ready') return reject('Дождитесь готовности стенда или завершения остановки.');
  if (runs.some(run => run.standId === stand.id && run.status === 'running')) {
    return reject('Прогон выполняется. Дождитесь завершения запросов.');
  }
  if (action.type === 'stop') {
    if (runs.some(run => run.standId === stand.id && run.collection === 'waiting')) {
      return reject('Дождитесь фиксации трасс перед остановкой стенда.');
    }
    return { ...state, message: '', stand: { ...stand, status: 'stopping' } };
  }
  if (action.type === 'fault') {
    if (action.enabled === Boolean(stand.fault)) return state;
    return { ...state, message: '', stand: {
      ...stand, fault: action.enabled ? paymentFault : null, faultRevision: stand.faultRevision + 1,
    } };
  }
  if (action.type === 'apply') {
    if (stand.id !== action.standId || stand.configRevision !== action.revision) {
      return reject('Ревизия конфига изменилась. Откройте новый предпросмотр и подтвердите его.');
    }
    if (stand.maxAttempts !== 3) return state;
    return { ...state, message: 'Изменение применено в демостенде. Ревизия конфига 13.',
      stand: { ...stand, maxAttempts: 2, configRevision: '13' } };
  }
  if (action.type === 'run') {
    const { versions } = action;
    if (versions.scenarioRevision !== stand.scenarioRevision || versions.faultRevision !== stand.faultRevision ||
        versions.configRevision !== stand.configRevision) {
      return reject('Версии стенда изменились. Проверьте текущие значения и запустите прогон снова.');
    }
    if (!stand.fault) return reject('Сначала задайте сбой payment.');
    const run: Run = { ...versions, id: action.id, standId: stand.id, fault: stand.fault,
      maxAttempts: stand.maxAttempts, status: 'running', collection: 'waiting',
      completed: 0, expected: loadCount, samples: [] };
    return { ...state, message: '', runs: [...runs, run] };
  }
  return state;
}

export function comparisonIssue(before: Run, after: Run): string {
  if (before.status === 'failed' || after.status === 'failed') return 'Один из прогонов завершился с ошибкой.';
  if (before.collection === 'waiting' || after.collection === 'waiting') return 'Дождитесь фиксации трасс обоих прогонов.';
  if (!before.samples.length || !after.samples.length) return 'Для сравнения нужны две непустые выборки.';
  if (before.id === after.id || before.standId !== after.standId ||
      before.scenarioRevision !== after.scenarioRevision || before.faultRevision !== after.faultRevision ||
      before.expected !== after.expected || before.maxAttempts === after.maxAttempts) {
    return 'Для сравнения нужны один стенд, одинаковые сценарий, сбой и нагрузка и изменение числа попыток.';
  }
  return '';
}
