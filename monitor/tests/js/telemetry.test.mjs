import test from 'node:test';
import assert from 'node:assert/strict';

import {
  JOINT_NAMES, MonitorRequestError, pollingDelay, isVector6, validateState,
  validateControlClient, hasLiveTelemetry, tcpSpeedMagnitudes, fetchJson,
  jointDisplay, prepareRobot, applyJointValues, createHistory, normalise,
  seriesPoints, seriesRange, createEventStream, createPoller,
  HISTORY_SAMPLES,
} from '../../static/telemetry.js';

function validState(overrides = {}) {
  return {
    connected: true,
    robot_ip: '192.0.2.10',
    monitor_ip: '192.0.2.20',
    last_update: 1_700_000_000.0,
    error: '',
    timestamp: 1234.5,
    actual_q: [0, 0, 0, 0, 0, 0],
    actual_TCP_pose: [0.1, 0.2, 0.3, 0, 0, 0],
    actual_TCP_speed: [0.03, 0.04, 0, 0, 0, 0],
    actual_joint_current: [1, 1, 1, 1, 1, 1],
    joint_temperatures: [40, 40, 40, 40, 40, 40],
    robot_mode: 7,
    safety_mode: 1,
    speed_scaling: 1,
    actual_digital_input_bits: 0,
    actual_digital_output_bits: 0,
    control_client: { active: true, reported: true, age_seconds: 0.2 },
    ...overrides,
  };
}

test('joint name list matches the URDF order', () => {
  assert.equal(JOINT_NAMES.length, 6);
  assert.equal(JOINT_NAMES[0], 'shoulder_pan_joint');
  assert.equal(JOINT_NAMES[5], 'wrist_3_joint');
});

test('pollingDelay backs off then saturates', () => {
  assert.equal(pollingDelay(0), 200);
  assert.equal(pollingDelay(1), 500);
  assert.equal(pollingDelay(2), 1000);
  assert.ok(pollingDelay(50) <= 5000);
});

test('isVector6 accepts only six finite numbers', () => {
  assert.ok(isVector6([0, 0, 0, 0, 0, 0]));
  assert.ok(!isVector6([0, 0, 0, 0, 0]));
  assert.ok(!isVector6([0, 0, 0, 0, 0, '0']));
  assert.ok(!isVector6([0, 0, 0, 0, 0, NaN]));
  assert.ok(!isVector6(null));
});

test('validateState accepts a well-formed frame', () => {
  assert.ok(validateState(validState()));
});

test('validateState rejects malformed frames', () => {
  const cases = [
    null,
    [],
    validState({ connected: 'yes' }),
    validState({ timestamp: 'soon' }),
    validState({ actual_q: [0, 0, 0] }),
    validState({ actual_TCP_pose: null }),
    validState({ actual_TCP_speed: [0, 0, 0, 0, 0] }),
    validState({ actual_joint_current: undefined }),
    validState({ joint_temperatures: [1, 2, 3, 4, 5, NaN] }),
    validState({ control_client: undefined }),
    validState({ control_client: { active: true, reported: true, age_seconds: 'x' } }),
  ];
  for (const value of cases) {
    assert.throws(() => validateState(value), MonitorRequestError, JSON.stringify(value));
  }
});

test('validateControlClient accepts null age but requires booleans', () => {
  assert.ok(validateControlClient({ active: false, reported: false, age_seconds: null }));
  assert.throws(() => validateControlClient({ active: 1, reported: true, age_seconds: 0 }), MonitorRequestError);
});

test('hasLiveTelemetry requires connection and a numeric timestamp', () => {
  assert.ok(hasLiveTelemetry(validState()));
  assert.ok(!hasLiveTelemetry(validState({ connected: false })));
  assert.ok(!hasLiveTelemetry(validState({ timestamp: null })));
  assert.ok(!hasLiveTelemetry(null));
});

test('tcpSpeedMagnitudes separates linear and angular parts', () => {
  const result = tcpSpeedMagnitudes([0.03, 0.04, 0, 0, 0, 0]);
  assert.equal(result.linear.toFixed(6), '0.050000');
  assert.equal(result.angular, 0);
  assert.equal(tcpSpeedMagnitudes(null), null);
});

test('jointDisplay scales within the nominal range', () => {
  const limit = { lower: -Math.PI * 2, upper: Math.PI * 2 };
  assert.equal(jointDisplay(0, limit).width, 0);
  assert.equal(jointDisplay(Math.PI, limit).width.toFixed(1), '50.0');
  assert.equal(jointDisplay(-Math.PI, limit).width.toFixed(1), '50.0');
  assert.ok(!jointDisplay(Math.PI, limit).outOfRange);
  assert.ok(jointDisplay(Math.PI * 2.5, limit).outOfRange);
  assert.equal(jointDisplay(Math.PI * 100, limit).width, 100);
});

test('jointDisplay honours an asymmetric nominal range', () => {
  const limit = { lower: -Math.PI, upper: Math.PI };
  assert.equal(jointDisplay(Math.PI, limit).width.toFixed(1), '100.0');
  assert.equal(jointDisplay(Math.PI / 2, limit).width.toFixed(1), '50.0');
  assert.ok(!jointDisplay(Math.PI, limit).outOfRange);
  assert.ok(jointDisplay(-Math.PI * 1.1, limit).outOfRange);
});

test('jointDisplay never clamps the reported degrees', () => {
  const limit = { lower: -1, upper: 1 };
  assert.equal(jointDisplay(4.2, limit).degrees.toFixed(1), '240.6');
  assert.ok(jointDisplay(4.2, limit).outOfRange);
});

test('prepareRobot disables planning limits for measured poses', () => {
  const joints = Object.fromEntries(JOINT_NAMES.map((name) => [name, {}]));
  prepareRobot({ joints });
  for (const name of JOINT_NAMES) assert.equal(joints[name].ignoreLimits, true);
  assert.doesNotThrow(() => prepareRobot({}));
});

test('applyJointValues writes each joint and ignores bad input', () => {
  const written = [];
  const robot = { setJointValue: (name, value) => written.push([name, value]) };
  applyJointValues(robot, [0, 1, 2, 3, 4, 5]);
  assert.deepEqual(written.map(([name]) => name), JOINT_NAMES);
  assert.equal(written[3][1], 3);
  written.length = 0;
  applyJointValues(robot, null);
  applyJointValues(null, [0, 0, 0, 0, 0, 0]);
  applyJointValues(robot, [0, 0, 0]);
  assert.equal(written.length, 0);
});

test('createHistory keeps a bounded window', () => {
  const history = createHistory(3);
  [1, 2, 3, 4, 5].forEach((value) => history.push(value));
  assert.equal(history.length, 3);
  assert.deepEqual(history.toArray(), [3, 4, 5]);
  assert.equal(history.at(0), 3);
  history.clear();
  assert.equal(history.length, 0);
});

test('history default capacity is a few minutes at 10 Hz', () => {
  assert.ok(HISTORY_SAMPLES >= 300);
});

test('normalise clamps and centres flat ranges', () => {
  assert.equal(normalise(5, 0, 10), 0.5);
  assert.equal(normalise(-5, 0, 10), 0);
  assert.equal(normalise(50, 0, 10), 1);
  assert.equal(normalise(7, 7, 7), 0.5);
  assert.equal(normalise(NaN, 0, 10), 0.5);
});

test('seriesRange pads a flat series instead of collapsing', () => {
  const range = seriesRange([2, 2, 2], 1);
  assert.ok(range.maximum > range.minimum);
  const empty = seriesRange([], 2);
  assert.deepEqual(empty, { minimum: -2, maximum: 2 });
});

test('seriesPoints maps the series across the full box', () => {
  const points = seriesPoints([0, 10], { width: 100, height: 50, minimum: 0, maximum: 10, padding: 0 });
  const pairs = points.split(' ').map((pair) => pair.split(',').map(Number));
  assert.equal(pairs.length, 2);
  assert.equal(pairs[0][0], 0);
  assert.equal(pairs[1][0], 100);
  // Highest value sits at the top of the browser coordinate space.
  assert.equal(pairs[0][1], 50);
  assert.equal(pairs[1][1], 0);
  assert.equal(seriesPoints([], { width: 10, height: 10, minimum: 0, maximum: 1 }), '');
});

test('fetchJson parses JSON and rejects error responses', async () => {
  const ok = { ok: true, text: async () => '{"a":1}' };
  assert.deepEqual(await fetchJson('/x', { fetchImpl: async () => ok }), { a: 1 });

  const bad = { ok: false, status: 500 };
  await assert.rejects(fetchJson('/x', { fetchImpl: async () => bad }), (error) => error.kind === 'http');

  const notJson = { ok: true, text: async () => 'nope' };
  await assert.rejects(fetchJson('/x', { fetchImpl: async () => notJson }), (error) => error.kind === 'response');
});

test('fetchJson reports network and timeout failures distinctly', async () => {
  await assert.rejects(
    fetchJson('/x', { fetchImpl: async () => { throw new Error('offline'); } }),
    (error) => error.kind === 'network',
  );
  const abortError = Object.assign(new Error('aborted'), { name: 'AbortError' });
  await assert.rejects(
    fetchJson('/x', { fetchImpl: async () => { throw abortError; } }),
    (error) => error.kind === 'timeout',
  );
});

/**
 * Drives createPoller synchronously: the injected scheduler records each tick
 * and lets the test decide when the next one runs.
 */
function manualScheduler({ onSchedule } = {}) {
  const scheduled = [];
  return {
    scheduled,
    schedule: (fn, delay) => {
      onSchedule?.(delay);
      scheduled.push({ fn, delay });
      return scheduled.length - 1;
    },
    cancel: () => {},
    async runNext() {
      // The first tick is started synchronously by start(); wait for its
      // finally-block to schedule the follow-up before pulling it.
      for (let attempt = 0; attempt < 50 && scheduled.length === 0; attempt += 1) {
        await new Promise((resolve) => setImmediate(resolve));
      }
      const next = scheduled.shift();
      if (!next) throw new Error('nothing scheduled');
      await next.fn();
      return next.delay;
    },
  };
}

test('createPoller backs off after failures and resets after a success', async () => {
  const queue = [new Error('boom'), new Error('boom'), 'ok'];
  const states = [];
  const errors = [];
  const delays = [];
  const scheduler = manualScheduler({ onSchedule: (delay) => delays.push(delay) });
  const poller = createPoller({
    request: async () => {
      const next = queue.shift();
      if (next instanceof Error) throw next;
      return next;
    },
    onState: (state) => states.push(state),
    onError: (error) => errors.push(error),
    schedule: scheduler.schedule,
    cancel: scheduler.cancel,
  });
  poller.start();
  // start() runs the first request straight away, so two more ticks cover the
  // second failure and the recovery.
  for (let tick = 0; tick < 2; tick += 1) await scheduler.runNext();
  poller.stop();
  // Two failures escalate the delay; the success resets it to the base interval.
  assert.deepEqual(delays.slice(0, 3), [500, 1000, 200]);
  assert.deepEqual(states, ['ok']);
  assert.equal(errors.length, 2);
});

test('createPoller ignores late results after stop', async () => {
  let resolveRequest = null;
  const states = [];
  const poller = createPoller({
    request: () => new Promise((resolve) => { resolveRequest = resolve; }),
    onState: (state) => states.push(state),
    onError: () => {},
    schedule: () => 0,
    cancel: () => {},
  });
  poller.start();
  await new Promise((resolve) => setImmediate(resolve));
  poller.stop();
  resolveRequest('late');
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(states.length, 0);
});

function fakeEventSource() {
  const instances = [];
  class Fake {
    constructor(url) {
      this.url = url;
      this.closed = false;
      this.onmessage = null;
      this.onerror = null;
      instances.push(this);
    }
    close() { this.closed = true; }
  }
  return { Fake, instances };
}

test('createEventStream validates pushed frames', () => {
  const { Fake, instances } = fakeEventSource();
  const states = [];
  const errors = [];
  const stream = createEventStream('/api/stream', {
    onState: (state) => states.push(state),
    onError: (error) => errors.push(error),
    EventSourceImpl: Fake,
  });
  assert.equal(instances.length, 1);
  assert.equal(instances[0].url, '/api/stream');
  instances[0].onmessage({ data: JSON.stringify(validState()) });
  instances[0].onmessage({ data: '{' });
  instances[0].onmessage({ data: JSON.stringify(validState({ connected: 'nope' })) });
  assert.equal(states.length, 1);
  assert.equal(errors.length, 2);
  assert.ok(errors.every((error) => error instanceof MonitorRequestError));
  stream.close();
  assert.equal(instances[0].closed, true);
});

test('createEventStream reports disconnects without closing', () => {
  const { Fake, instances } = fakeEventSource();
  const errors = [];
  const stream = createEventStream('/api/stream', {
    onState: () => {},
    onError: (error) => errors.push(error),
    EventSourceImpl: Fake,
  });
  instances[0].onerror();
  assert.equal(errors.length, 1);
  assert.equal(errors[0].kind, 'network');
  assert.equal(instances[0].closed, false);
  stream.close();
});

test('createEventStream returns null when EventSource is unavailable', () => {
  assert.equal(createEventStream('/api/stream', { onState: () => {}, onError: () => {}, EventSourceImpl: undefined }), null);
});
