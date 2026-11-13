import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../backend/app/static/route-progress.js', import.meta.url));
const { calculateRouteProgress } = await import(`data:text/javascript;base64,${source.toString('base64')}`);
const fixture = () => ({
  shipment: { current_leg_id: 'sea' },
  plan: { legs: [{ id: 'rail' }, { id: 'sea' }], completed_leg_ids: ['rail'] },
  baseline: { legs: [{ id: 'sea', in_transit: true, remaining_h: 6 }] },
  horizon: { as_of: '2026-11-13T12:00:00Z' },
  events: [{ id: 'dep', leg_id: 'sea', event_type: 'departure', source: 'operator-manual', actual_ts: '2026-11-13T06:00:00Z' }],
});
test('recorded departure positions an in-transit cargo between ports', () => {
  assert.equal(calculateRouteProgress(fixture()), .75);
});
test('future horizon reaches the destination without writing events', () => {
  const data = fixture();
  const before = JSON.stringify(data);
  assert.equal(calculateRouteProgress(data, data.plan, data.baseline, 6), 1);
  assert.equal(JSON.stringify(data), before);
});
test('superseded, future and demo departures cannot invent progress', () => {
  for (const type of ['corrected', 'future', 'demo']) {
    const data = fixture();
    if (type === 'corrected') data.events.push({ id: 'correction', correction_of: 'dep' });
    if (type === 'future') data.events[0].actual_ts = '2026-11-14T00:00:00Z';
    if (type === 'demo') data.events[0].source = 'demo-simulation';
    assert.equal(calculateRouteProgress(data), .5, type);
  }
});
test('port waiting and unavailable projection leave cargo at the last known node', () => {
  const data = fixture();
  data.baseline.legs[0] = { id: 'sea', remaining_h: 12, wait_h: 8, in_transit: false };
  assert.equal(calculateRouteProgress(data, data.plan, data.baseline, 4), .5);
  data.baseline.legs[0].remaining_h = null;
  assert.equal(calculateRouteProgress(data), .5);
});
test('delivered and empty plans have stable boundaries', () => {
  const data = fixture();
  data.plan.completed_leg_ids.push('sea');
  assert.equal(calculateRouteProgress(data), 1);
  assert.equal(calculateRouteProgress({ plan: { legs: [] } }), 0);
});
