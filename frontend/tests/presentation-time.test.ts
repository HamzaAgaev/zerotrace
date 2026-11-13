import test from 'node:test';
import assert from 'node:assert/strict';
import { changeValue, fieldLabel, timingText, nanosecondsAsMilliseconds } from '../src/presentation.js';

test('plan and journal convert numeric and JSON-encoded timeout values to milliseconds', () => {
  assert.equal(fieldLabel('calls.payment.per_attempt_timeout_ns'), 'Таймаут попытки, мс');
  assert.equal(changeValue('per_attempt_timeout_ns', '"800000000"'), '800');
  assert.equal(changeValue('calls.payment.timeout_ns', '1500000'), '1,5');
  assert.equal(changeValue('timeout_ns', '1'), '0,000001');
  assert.equal(changeValue('max_attempts', '3'), '3');
  assert.equal(changeValue('timeout_ms', '800'), '800');
  assert.equal(changeValue('timeout_ns', 'null'), 'null');
  assert.equal(nanosecondsAsMilliseconds('9007199254740993').replace(/\s/g, ''), '9007199254,740993');
});

test('audit text from backend is displayed in milliseconds without altering machine keys', () => {
  assert.equal(timingText('Требуется 3150000000 нс при доступных 2000000000 нс.').replace(/\s/g, ' '), 'Требуется 3 150 мс при доступных 2 000 мс.');
  assert.equal(timingText('Таймаут 1 500 000 ns, пауза 0 нс.'), 'Таймаут 1,5 мс, пауза 0 мс.');
  assert.equal(timingText('timeout_ns: 800000000; 800 мс'), 'timeout_ns: 800000000; 800 мс');
});
