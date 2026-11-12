import test from 'node:test';
import assert from 'node:assert/strict';
import {exampleScenario,parseScenario} from '../src/labScenario.js';
import {createDemoLab} from '../src/api/demoLab.js';
test('explicit scenario preserves supplied policies in demo and rejects dangling edges',async()=>{
 const scenario=parseScenario(JSON.stringify(exampleScenario));
 scenario.services.api.routes['POST /run'].calls.first.timeout_ms=123;
 const client=createDemoLab();
 const stand=await client.createStand({scenario_hash:(await client.validateScenario({scenario})).scenario_hash},undefined,'create-id');
 assert.equal(stand.creation_request_id,'create-id');
 assert.equal((await client.getConfig('api')).calls[0].timeout_ms,123);
 scenario.entrypoint.route='GET /missing';
 assert.throws(()=>parseScenario(JSON.stringify(scenario)),/entrypoint/);
});
