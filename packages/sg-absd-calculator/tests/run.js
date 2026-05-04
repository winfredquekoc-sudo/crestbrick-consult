'use strict';

const assert = require('assert');
const { calculateAbsd, computeBsd, absdRate } = require('../src/index.js');

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log('  ok   ' + name);
    passed++;
  } catch (e) {
    console.log('  FAIL ' + name + '\n       ' + e.message);
    failed++;
  }
}

console.log('sg-absd-calculator — tests');

test('SC first property: BSD only, no ABSD', function () {
  const r = calculateAbsd({ price: 1000000, citizenship: 'SC', propertyCount: 1 });
  assert.strictEqual(r.absd, 0);
  assert.strictEqual(r.rate, 0);
  // BSD on $1M: 180k*1% + 180k*2% + 640k*3% = 1800 + 3600 + 19200 = 24600
  assert.strictEqual(r.bsd, 24600);
  assert.strictEqual(r.amount, 24600);
});

test('SC second property: 20% ABSD', function () {
  const r = calculateAbsd({ price: 2000000, citizenship: 'SC', propertyCount: 2 });
  assert.strictEqual(r.rate, 0.20);
  assert.strictEqual(r.absd, 400000);
});

test('PR first property: 5% ABSD', function () {
  const r = calculateAbsd({ price: 1500000, citizenship: 'PR', propertyCount: 1 });
  assert.strictEqual(r.rate, 0.05);
  assert.strictEqual(r.absd, 75000);
});

test('Foreigner: 60% ABSD regardless of property count', function () {
  const r = calculateAbsd({ price: 3000000, citizenship: 'FOR', propertyCount: 1 });
  assert.strictEqual(r.rate, 0.60);
  assert.strictEqual(r.absd, 1800000);
});

test('Entity: 65% ABSD', function () {
  const r = calculateAbsd({ price: 5000000, citizenship: 'ENT', propertyCount: 1 });
  assert.strictEqual(r.rate, 0.65);
  assert.strictEqual(r.absd, 3250000);
  // BSD on 5M: tiers fully consume at 1.5M*5%=75000, then 2M over 3M cap → mixed
  // 1800 + 3600 + 19200 + 20000 + 75000 + (5000000-3000000)*0.06 = 119600 + 120000 = 239600
  assert.strictEqual(r.bsd, 239600);
});

console.log('\n  ' + passed + ' passed, ' + failed + ' failed');
process.exit(failed === 0 ? 0 : 1);
