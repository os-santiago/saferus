import test from 'node:test';
import assert from 'node:assert/strict';
import { formatTTL } from '../web/static/ttl.js';

test('token lifetime is readable without changing its value', () => {
  assert.equal(formatTTL(60), '60 seconds');
  assert.equal(formatTTL(0), '0 seconds');
});
test('invalid lifetimes are unavailable', () => {
  for (const value of [-1, 1.5, NaN, Infinity, '60', null, undefined]) {
    assert.equal(formatTTL(value), 'Unavailable');
  }
});
