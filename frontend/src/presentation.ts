/** Shared human-facing vocabulary. Machine keys remain untouched in payloads and exports. */

export const statusLabels: Record<string, string> = {
  ready: 'Готов', building: 'Создаётся', stopping: 'Останавливается', stopped: 'Остановлен',
  unavailable: 'Недоступен', failed: 'Ошибка', completed: 'Завершён', running: 'Выполняется',
  pending: 'Ожидает запуска', waiting: 'Ожидаем трейсы', partial: 'Получена часть трейсов',
  collecting: 'Собираем трейсы', applied: 'Применено', unknown: 'Результат неизвестен',
  observed: 'Измерено', planned: 'Запланировано', applying: 'Применяется', rejected: 'Отклонено',
  no_op: 'Без изменений', checking: 'Проверяем', insufficient_data: 'Недостаточно данных',
};

/** Applied configuration and the observed symptom are deliberately different facts. */
export const journalStatusLabels: Record<string, string> = {
  planned: 'План подготовлен', applying: 'Изменения применяются', applied: 'Настройки применены',
  no_op: 'Настройки уже соответствовали плану', rejected: 'Изменение отклонено',
  unknown: 'Результат изменения неизвестен', failed: 'Применить настройки не удалось',
};

export const verificationLabels: Record<string, string> = {
  checking: 'Проверка выполняется', insufficient_data: 'Недостаточно данных для проверки',
  symptom_absent: 'Проблема больше не наблюдается', symptom_present: 'Проблема сохраняется',
  inconclusive: 'Результат проверки неясен',
};

export const fieldLabels: Record<string, string> = {
  timeout_ms: 'Таймаут попытки, мс', per_attempt_timeout_ns: 'Таймаут попытки, нс',
  per_attempt_timeout_ms: 'Таймаут попытки, мс', timeout_ns: 'Таймаут попытки, нс',
  max_attempts: 'Всего попыток, включая первую', backoff_ms: 'Пауза между попытками, мс',
  retry_on: 'Условия повторного запроса', work: 'Время обработки запроса',
  delay_ms: 'Время обработки запроса, мс', p50_ms: 'Обычное время (медиана), мс', p99_ms: 'Время для 99% запросов, мс',
};

export const fieldLabel = (name: string) => {
  if (fieldLabels[name]) return fieldLabels[name];
  if (name.endsWith('work.delay_ms')) return 'Время обработки запроса, мс';
  if (name.endsWith('work.p50_ms')) return 'Медиана времени обработки, мс';
  if (name.endsWith('work.p99_ms')) return 'Время для 99% запросов, мс';
  if (name.endsWith('work.kind')) return 'Характер времени обработки';
  const short = name.split('.').at(-1) ?? name;
  return fieldLabels[short] ?? name;
};

export const statusLabel = (name: string) => statusLabels[name] ?? name;
export const journalStatusLabel = (name: string) => journalStatusLabels[name] ?? name;
export const verificationLabel = (name: string | null) =>
  name === null ? 'Не проверялась' : verificationLabels[name] ?? name;

export const scenarioLabels: Record<string, string> = { checkout: 'Магазин — демопример', generated: 'По выбранному трейсу' };
export const scenarioLabel = (id: string) => scenarioLabels[id] ?? id;

const milliseconds = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
const counts = new Intl.NumberFormat('ru-RU');
const percentages = new Intl.NumberFormat('ru-RU', { style: 'percent', maximumFractionDigits: 1 });

/** Shared duration formatting for values already expressed in milliseconds. */
export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'Неизвестно';
  return `${milliseconds.format(value)}\u00A0мс`;
}
export const formatCount = (value: number | null | undefined) =>
  value === null || value === undefined || !Number.isFinite(value) ? '—' : counts.format(value);
export const formatPercent = (value: number | null | undefined) =>
  value === null || value === undefined || !Number.isFinite(value) ? '—' : percentages.format(value);
