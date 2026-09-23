/**
 * Tests for the trajectory, latency, jump-detection, replay, theme and baseline
 * helpers added in the 3.1 front-end refactor.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  isTrajectoryPoint, parseTrajectory, trajectoryTrack, decimateTrack, trackLength,
  parseLatency, latencyModeLabel, jointJumps, exceedsJumpThreshold, formatLatency,
  alignSeries, MonitorRequestError,
} from '../../static/telemetry.js';
import { createReplay, DEFAULT_SPEEDS } from '../../static/replay.js';
import {
  isTheme, nextTheme, applyTheme, readStoredTheme, storeTheme, THEMES,
} from '../../static/theme.js';

const point = (x = 0, y = 0, z = 0.5) => [x, y, z, 0, 0, 0, 0, 0, 0];

function fakeStorage(initial = {}) {
  const data = { ...initial };
  return {
    getItem: (key) => (key in data ? data[key] : null),
    setItem: (key, value) => { data[key] = String(value); },
    _data: data,
  };
}

function fakeDoc() {
  const attributes = {};
  return {
    documentElement: {
      setAttribute: (name, value) => { attributes[name] = value; },
      style: {},
      getAttribute: (name) => attributes[name] ?? null,
    },
    _attributes: attributes,
  };
}

// --- Trajectory ----------------------------------------------------------

test('isTrajectoryPoint accepts a full 9-value point', () => {
  assert.equal(isTrajectoryPoint(point()), true);
  assert.equal(isTrajectoryPoint([1, 2, 3]), false);
  assert.equal(isTrajectoryPoint([1, 2, 3, 4, 5, 6, 7, 8, 'x']), false);
  assert.equal(isTrajectoryPoint(null), false);
});

test('parseTrajectory rejects a malformed payload', () => {
  assert.throws(() => parseTrajectory(null), MonitorRequestError);
  assert.throws(() => parseTrajectory({ status: {} }), MonitorRequestError);
});

test('parseTrajectory drops malformed points but keeps valid ones', () => {
  const payload = parseTrajectory({ status: { points: 3 }, points: [point(), 'bad', point(1, 2, 3)] });
  assert.equal(payload.points.length, 2);
  assert.equal(payload.status.points, 3);
});

test('trajectoryTrack extracts xyz triples', () => {
  const track = trajectoryTrack([point(1, 2, 3), point(4, 5, 6)]);
  assert.deepEqual(track, [[1, 2, 3], [4, 5, 6]]);
});

test('decimateTrack preserves endpoints when downsampling', () => {
  const track = Array.from({ length: 1000 }, (_, index) => [index, 0, 0]);
  const reduced = decimateTrack(track, 100);
  assert.equal(reduced.length, 100);
  assert.deepEqual(reduced[0], [0, 0, 0]);
  assert.deepEqual(reduced.at(-1), [999, 0, 0]);
});

test('decimateTrack returns the input untouched when small enough', () => {
  const track = [[0, 0, 0], [1, 0, 0]];
  assert.deepEqual(decimateTrack(track, 100), track);
});

test('decimateTrack filters malformed entries', () => {
  const reduced = decimateTrack([[0, 0, 0], ['x', 1, 2], [1, 1, 1]], 100);
  assert.equal(reduced.length, 2);
});

test('trackLength sums 3D segment lengths', () => {
  assert.equal(trackLength([[0, 0, 0], [3, 4, 0]]), 5);
  assert.equal(trackLength([]), 0);
});

// --- Latency -------------------------------------------------------------

test('formatLatency switches units by magnitude', () => {
  assert.equal(formatLatency(0.4), '400 µs');
  assert.equal(formatLatency(12.34), '12.3 ms');
  assert.equal(formatLatency(2500), '2.50 s');
  assert.equal(formatLatency(null), '—');
});

test('latencyModeLabel explains both measurement modes', () => {
  assert.match(latencyModeLabel('monotonic'), /Monotonic/);
  assert.match(latencyModeLabel('clock_sync'), /Clock-synced/);
  assert.match(latencyModeLabel('other'), /Unavailable/);
});

test('parseLatency passes null through and rejects junk', () => {
  assert.equal(parseLatency(null), null);
  assert.equal(parseLatency(undefined), null);
  assert.deepEqual(parseLatency({ last_ms: 5 }), { last_ms: 5 });
  assert.throws(() => parseLatency('slow'), MonitorRequestError);
});

// --- Jump detection ------------------------------------------------------

test('jointJumps returns per-joint absolute deltas', () => {
  const jumps = jointJumps([0, 0, 0, 0, 0, 0], [0.1, 0, 0, 0, 0, 0]);
  assert.equal(jumps[0], 0.1);
  assert.equal(jumps[1], 0);
});

test('jointJumps returns null when a vector is missing', () => {
  assert.equal(jointJumps(null, [0, 0, 0, 0, 0, 0]), null);
  assert.equal(jointJumps([0, 0, 0, 0, 0, 0], [1, 2, 3]), null);
});

test('exceedsJumpThreshold compares in degrees', () => {
  const twentyDegrees = 20 * Math.PI / 180;
  const thirtyDegrees = 30 * Math.PI / 180;
  assert.equal(exceedsJumpThreshold([twentyDegrees], 25), false);
  assert.equal(exceedsJumpThreshold([thirtyDegrees], 25), true);
  // Thresholds are inclusive of the boundary only when strictly greater.
  assert.equal(exceedsJumpThreshold([25 * Math.PI / 180], 25), false);
});

test('exceedsJumpThreshold is false for non-arrays and bad thresholds', () => {
  assert.equal(exceedsJumpThreshold(null, 25), false);
  assert.equal(exceedsJumpThreshold([1], NaN), false);
});

// --- Baseline alignment --------------------------------------------------

test('alignSeries downsamples both sides to a shared length', () => {
  const left = Array.from({ length: 500 }, (_, index) => index);
  const right = Array.from({ length: 1000 }, (_, index) => index);
  const aligned = alignSeries(left, right, 100);
  assert.equal(aligned.left.length, 100);
  assert.equal(aligned.right.length, 100);
  assert.equal(aligned.left[0], 0);
  assert.equal(aligned.right.at(-1), 999);
});

test('alignSeries keeps short series intact', () => {
  const aligned = alignSeries([1, 2, 3], [4, 5], 100);
  assert.deepEqual(aligned.left, [1, 2, 3]);
  assert.deepEqual(aligned.right, [4, 5]);
});

test('alignSeries drops non-finite values', () => {
  const aligned = alignSeries([1, NaN, 3], [null, 2], 10);
  assert.deepEqual(aligned.left, [1, 3]);
  assert.deepEqual(aligned.right, [2]);
});

// --- Replay --------------------------------------------------------------

test('createReplay exposes the recorded frame count', () => {
  const replay = createReplay([point(), point(1, 0, 0)]);
  assert.equal(replay.length, 2);
  assert.equal(replay.index, 0);
  assert.equal(replay.playing, false);
});

test('createReplay seek clamps into range and emits a frame', () => {
  const frames = [];
  const replay = createReplay([point(0), point(1), point(2)], {
    onFrame: (frame, index) => frames.push([frame[0], index]),
  });
  replay.seek(0.5);
  assert.equal(replay.index, 1);
  assert.deepEqual(frames.at(-1), [1, 1]);
  replay.seek(5);
  assert.equal(replay.index, 2);
  replay.seek(-3);
  assert.equal(replay.index, 0);
});

test('createReplay setSpeed rejects nonsense and keeps the last good value', () => {
  const replay = createReplay([point(), point(1)], { speed: 2 });
  assert.equal(replay.setSpeed(4), 4);
  assert.equal(replay.setSpeed(0), 1);
  assert.equal(replay.setSpeed(NaN), 1);
});

test('createReplay reset re-emits the first frame', () => {
  const frames = [];
  const replay = createReplay([point(0), point(1), point(2)], {
    onFrame: (frame) => frames.push(frame[0]),
  });
  replay.seek(1);
  replay.reset();
  assert.equal(replay.index, 0);
  assert.equal(frames.at(-1), 0);
});

test('createReplay play is a no-op for a single frame', () => {
  const replay = createReplay([point()]);
  assert.equal(replay.play(), false);
  assert.equal(replay.playing, false);
});

test('DEFAULT_SPEEDS offers a usable range', () => {
  assert.ok(DEFAULT_SPEEDS.includes(1));
  assert.ok(Math.min(...DEFAULT_SPEEDS) < 1);
  assert.ok(Math.max(...DEFAULT_SPEEDS) > 1);
});

// --- Theme ---------------------------------------------------------------

test('THEMES contains exactly dark and light', () => {
  assert.deepEqual(THEMES, ['dark', 'light']);
  assert.equal(isTheme('light'), true);
  assert.equal(isTheme('neon'), false);
});

test('nextTheme flips between the two values', () => {
  assert.equal(nextTheme('dark'), 'light');
  assert.equal(nextTheme('light'), 'dark');
});

test('applyTheme writes the attribute and coerces unknown values', () => {
  const doc = fakeDoc();
  assert.equal(applyTheme(doc, 'light'), 'light');
  assert.equal(doc._attributes['data-theme'], 'light');
  assert.equal(applyTheme(doc, 'nonsense'), 'dark');
  assert.equal(doc._attributes['data-theme'], 'dark');
});

test('readStoredTheme returns only valid stored values', () => {
  assert.equal(readStoredTheme(fakeStorage({ 'ur-monitor-theme': 'light' })), 'light');
  assert.equal(readStoredTheme(fakeStorage({ 'ur-monitor-theme': 'neon' })), null);
  assert.equal(readStoredTheme(fakeStorage()), null);
});

test('readStoredTheme survives a storage that throws', () => {
  const hostile = { getItem() { throw new Error('blocked'); } };
  assert.equal(readStoredTheme(hostile), null);
});

test('storeTheme persists and reports storage failure', () => {
  const storage = fakeStorage();
  assert.equal(storeTheme(storage, 'light'), true);
  assert.equal(storage._data['ur-monitor-theme'], 'light');
  const hostile = { setItem() { throw new Error('quota'); } };
  assert.equal(storeTheme(hostile, 'light'), false);
});
