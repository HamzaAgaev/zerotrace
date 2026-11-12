import test from 'node:test';
import assert from 'node:assert/strict';
import {serviceColor,traceServiceColors} from '../src/lib.js';

test('service colors match across graph and trace subsets',()=>{
  const services=['gateway','orders','payment','inventory','pricing','delivery','fraud','bank-api','notifications','custom-service'];
  const colors=traceServiceColors(services);
  assert.equal(new Set(colors.values()).size,services.length);
  for(const name of services){
    assert.equal(colors.get(name),serviceColor(name));
    assert.equal(traceServiceColors([name]).get(name),colors.get(name));
    assert.equal(traceServiceColors([...services].reverse()).get(name),colors.get(name));
  }
});
