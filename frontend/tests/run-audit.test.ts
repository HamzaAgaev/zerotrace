import test from 'node:test';
import assert from 'node:assert/strict';
import { validatePayload } from '../src/api/client.js';
import { demoAudit } from '../src/api/fixtures.js';

test('test results accept audit findings with all exact trace references',()=>{
  const finding=demoAudit.findings[0];
  const payload={analyzed_traces:12,missing_traces:0,findings:[{...finding,trace_refs:finding.exemplars}],rule_evaluations:demoAudit.rule_evaluations};
  assert.doesNotThrow(()=>validatePayload('run-services',payload));
  assert.throws(()=>validatePayload('run-services',{...payload,findings:[{...finding,trace_refs:[{trace_id:'bad'}]}]}));
  assert.throws(()=>validatePayload('run-services',{analyzed_traces:1,missing_traces:0,services:[]}));
});
