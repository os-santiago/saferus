import test from 'node:test';
import assert from 'node:assert/strict';
import { formatTTL } from '../web/static/ttl.js';

test('token lifetime is readable without changing its value', () => {
  assert.equal(formatTTL(0), '0 seconds');
  assert.equal(formatTTL(1), '1 second');
  assert.equal(formatTTL(59), '59 seconds');
  assert.equal(formatTTL(60), '1 minute');
  assert.equal(formatTTL(61), '1 minute 1 second');
  assert.equal(formatTTL(120), '2 minutes');
  assert.equal(formatTTL(3600), '1 hour');
  assert.equal(formatTTL(3661), '1 hour 1 minute 1 second');
  assert.equal(formatTTL(86400), '1 day');
  assert.equal(formatTTL(90061), '1 day 1 hour 1 minute 1 second');
  assert.equal(formatTTL(Number.MAX_SAFE_INTEGER), '104249991374 days 7 hours 36 minutes 31 seconds');
});
test('invalid lifetimes are unavailable', () => {
  for (const value of [-1, 1.5, NaN, Infinity, '60', null, undefined]) {
    assert.equal(formatTTL(value), 'Unavailable');
  }
  assert.equal(formatTTL(Number.MAX_SAFE_INTEGER + 1), 'Unavailable');
  assert.equal(formatTTL(-Infinity), 'Unavailable');
});
