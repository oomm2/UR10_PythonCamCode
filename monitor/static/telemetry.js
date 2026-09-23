export const JOINT_NAMES = [
  'shoulder_pan_joint', 'shoulder_lift_joint', 'elbow_joint',
  'wrist_1_joint', 'wrist_2_joint', 'wrist_3_joint',
];
export const POLL_INTERVAL_MS = 200;
export const REQUEST_TIMEOUT_MS = 1500;
export const HISTORY_SAMPLES = 600;

export class MonitorRequestError extends Error {
  constructor(kind, message) {
    super(message);
    this.name = 'MonitorRequestError';
    this.kind = kind;
  }
}

export function pollingDelay(failures) {
  return failures === 0 ? POLL_INTERVAL_MS : Math.min(5000, 500 * 2 ** Math.min(failures - 1, 4));
}

export function isVector6(value) {
  return Array.isArray(value) && value.length === 6 && value.every(Number.isFinite);
}

export function isFiniteOrNull(value) {
  return value === null || Number.isFinite(value);
}

export function validateControlClient(control) {
  if (!control || typeof control !== 'object' || Array.isArray(control)
    || typeof control.active !== 'boolean' || typeof control.reported !== 'boolean'
    || !isFiniteOrNull(control.age_seconds)) {
    throw new MonitorRequestError('response', 'Monitor returned an invalid control client object');
  }
  return control;
}

export function validateState(state) {
  if (!state || typeof state !== 'object' || Array.isArray(state)
    || typeof state.connected !== 'boolean'
    || !isFiniteOrNull(state.timestamp) || !isFiniteOrNull(state.last_update)
    || !isFiniteOrNull(state.robot_mode) || !isFiniteOrNull(state.safety_mode)
    || !isFiniteOrNull(state.speed_scaling)
    || !isVector6(state.actual_q) || !isVector6(state.actual_TCP_pose)
    || !isVector6(state.actual_TCP_speed) || !isVector6(state.actual_joint_current)
    || !isVector6(state.joint_temperatures)) {
    throw new MonitorRequestError('response', 'Monitor returned an invalid state object');
  }
  validateControlClient(state.control_client);
  return state;
}

export function hasLiveTelemetry(state) {
  // A stale or disconnected frame must not be presented as a live pose.
  return Boolean(state) && state.connected === true && Number.isFinite(state.timestamp);
}

export function tcpSpeedMagnitudes(speed) {
  if (!isVector6(speed)) return null;
  const magnitude = (values) => Math.sqrt(values.reduce((total, value) => total + value * value, 0));
  return { linear: magnitude(speed.slice(0, 3)), angular: magnitude(speed.slice(3, 6)) };
}

export async function fetchJson(url, { fetchImpl = fetch, timeoutMs = REQUEST_TIMEOUT_MS } = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(url, { cache: 'no-store', signal: controller.signal });
    if (!response.ok) {
      throw new MonitorRequestError('http', `Monitor returned HTTP ${response.status}`);
    }
    const text = await response.text();
    try {
      return JSON.parse(text);
    } catch {
      throw new MonitorRequestError('response', 'Monitor returned invalid JSON');
    }
  } catch (error) {
    if (error instanceof MonitorRequestError) throw error;
    if (controller.signal.aborted || error.name === 'AbortError') {
      throw new MonitorRequestError('timeout', 'Monitor request timed out');
    }
    throw new MonitorRequestError('network', 'Monitor is unreachable');
  } finally {
    clearTimeout(timeout);
  }
}

export function jointDisplay(value, limit) {
  return {
    degrees: value * 180 / Math.PI,
    width: Math.min(100, Math.abs(value) / Math.max(Math.abs(limit.lower), Math.abs(limit.upper)) * 100),
    outOfRange: value < limit.lower || value > limit.upper,
  };
}

export function prepareRobot(robot) {
  // Measured poses must not be clamped to the URDF's planning limits.
  JOINT_NAMES.forEach((name) => {
    if (robot.joints?.[name]) robot.joints[name].ignoreLimits = true;
  });
}

export function applyJointValues(robot, values) {
  if (!robot || !isVector6(values)) return;
  JOINT_NAMES.forEach((name, index) => robot.setJointValue(name, values[index]));
}

/**
 * Bounded FIFO of samples used by the trend charts.
 * Keeps memory flat for a dashboard that runs for hours.
 */
export function createHistory(capacity = HISTORY_SAMPLES) {
  const samples = [];
  return {
    push(sample) {
      samples.push(sample);
      if (samples.length > capacity) samples.splice(0, samples.length - capacity);
    },
    clear() {
      samples.length = 0;
    },
    get length() {
      return samples.length;
    },
    at(index) {
      return samples[index];
    },
    toArray() {
      return samples.slice();
    },
  };
}

/**
 * Map a value into a normalised 0..1 plot coordinate.
 * Flat series are centred rather than dividing by zero.
 */
export function normalise(value, minimum, maximum) {
  if (!Number.isFinite(value) || !Number.isFinite(minimum) || !Number.isFinite(maximum)) return 0.5;
  if (maximum - minimum < 1e-9) return 0.5;
  return Math.min(1, Math.max(0, (value - minimum) / (maximum - minimum)));
}

/**
 * Build polyline points for one series inside a pixel box.
 * Returns an SVG-ready "x,y x,y" string.
 */
export function seriesPoints(values, { width, height, minimum, maximum, padding = 4 }) {
  if (!Array.isArray(values) || values.length === 0) return '';
  const usableWidth = Math.max(1, width - padding * 2);
  const usableHeight = Math.max(1, height - padding * 2);
  const step = values.length > 1 ? usableWidth / (values.length - 1) : 0;
  return values.map((value, index) => {
    const x = padding + step * index;
    const y = padding + usableHeight * (1 - normalise(value, minimum, maximum));
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(' ');
}

export function seriesRange(values, fallbackPad = 1) {
  const finite = (values || []).filter(Number.isFinite);
  if (finite.length === 0) return { minimum: -fallbackPad, maximum: fallbackPad };
  let minimum = Math.min(...finite);
  let maximum = Math.max(...finite);
  if (maximum - minimum < 1e-6) {
    minimum -= fallbackPad / 2;
    maximum += fallbackPad / 2;
  }
  return { minimum, maximum };
}

/**
 * Subscribe to the monitor's SSE feed.
 *
 * Returns null when EventSource is unavailable so the caller can fall back to
 * request/response polling — the dashboard must still work on older browsers.
 */
export function createEventStream(url, { onState, onError, EventSourceImpl } = {}) {
  const Source = EventSourceImpl ?? (typeof EventSource === 'function' ? EventSource : null);
  if (!Source) return null;
  let source = null;
  let closed = false;
  const connect = () => {
    if (closed) return;
    source = new Source(url);
    source.onmessage = (event) => {
      try {
        onState(validateState(JSON.parse(event.data)));
      } catch (error) {
        onError(error instanceof MonitorRequestError
          ? error
          : new MonitorRequestError('response', `Invalid stream payload: ${error.message}`));
      }
    };
    source.onerror = () => {
      // EventSource reconnects on its own; surface the gap without tearing down.
      onError(new MonitorRequestError('network', 'Monitor stream disconnected'));
    };
  };
  connect();
  return {
    close() {
      closed = true;
      source?.close();
    },
  };
}

export function createPoller({ request, onState, onError, schedule = setTimeout, cancel = clearTimeout }) {
  let timer = null;
  let running = false;
  let failures = 0;
  async function tick() {
    try {
      const state = await request();
      if (!running) return;
      onState(state);
      failures = 0;
    } catch (error) {
      if (!running) return;
      failures += 1;
      onError(error);
    } finally {
      if (running) timer = schedule(tick, pollingDelay(failures));
    }
  }
  return {
    start() {
      if (running) return;
      running = true;
      tick();
    },
    stop() {
      running = false;
      cancel(timer);
    },
  };
}

// ---------------------------------------------------------------------------
// Trajectory, latency and jump-detection helpers.
//
// These are deliberately pure so they can be unit tested without a browser and
// reused by both the live path and the recording replay path.
// ---------------------------------------------------------------------------

/** Validate one trajectory point: x, y, z plus six joint angles. */
export function isTrajectoryPoint(point) {
  return Array.isArray(point) && point.length >= 9 && point.slice(0, 9).every(Number.isFinite);
}

/** Validate a /api/trajectory payload, dropping any malformed points. */
export function parseTrajectory(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new MonitorRequestError('response', 'Monitor returned an invalid trajectory object');
  }
  if (!Array.isArray(payload.points)) {
    throw new MonitorRequestError('response', 'Monitor trajectory is missing a points array');
  }
  return {
    status: payload.status && typeof payload.status === 'object' ? payload.status : {},
    points: payload.points.filter(isTrajectoryPoint),
  };
}

/** Extract the XYZ track from trajectory points for the 3D trail. */
export function trajectoryTrack(points) {
  return (points || []).filter(isTrajectoryPoint).map((point) => [point[0], point[1], point[2]]);
}

/**
 * Resample a track to at most ``maxPoints`` entries by even index selection.
 * Keeps the first and last sample so the drawn path never loses its endpoints.
 */
export function decimateTrack(track, maxPoints = 4000) {
  const source = (track || []).filter((point) => Array.isArray(point) && point.length >= 3
    && point.slice(0, 3).every(Number.isFinite));
  if (source.length <= maxPoints) return source.slice();
  const step = (source.length - 1) / (maxPoints - 1);
  const result = [];
  for (let index = 0; index < maxPoints; index += 1) {
    result.push(source[Math.round(index * step)]);
  }
  return result;
}

/** Path length of a 3D track in metres. */
export function trackLength(track) {
  let total = 0;
  for (let index = 1; index < (track || []).length; index += 1) {
    const previous = track[index - 1];
    const current = track[index];
    total += Math.hypot(current[0] - previous[0], current[1] - previous[1], current[2] - previous[2]);
  }
  return total;
}

/** Validate the latency block attached to a state snapshot. */
export function parseLatency(latency) {
  if (latency === null || latency === undefined) return null;
  if (typeof latency !== 'object' || Array.isArray(latency)) {
    throw new MonitorRequestError('response', 'Monitor returned an invalid latency object');
  }
  return latency;
}

/** Human label for the active latency measurement mode. */
export function latencyModeLabel(mode) {
  if (mode === 'clock_sync') return 'Clock-synced (Mac → Windows)';
  if (mode === 'monotonic') return 'Monotonic (arrival delta)';
  return 'Unavailable';
}

/**
 * Largest single-step change per joint across consecutive samples.
 * Used to flag RTDE packet loss, which shows up as an impossible jump.
 */
export function jointJumps(previous, current) {
  if (!isVector6(previous) || !isVector6(current)) return null;
  return current.map((value, index) => Math.abs(value - previous[index]));
}

/** True when any joint moved more than ``thresholdDeg`` in one sample. */
export function exceedsJumpThreshold(jumps, thresholdDeg) {
  if (!Array.isArray(jumps) || !Number.isFinite(thresholdDeg)) return false;
  const threshold = thresholdDeg * Math.PI / 180;
  return jumps.some((value) => Number.isFinite(value) && value > threshold);
}

/** Format a latency figure for a compact metric tile. */
export function formatLatency(ms) {
  if (!Number.isFinite(ms)) return '—';
  if (ms < 1) return `${(ms * 1000).toFixed(0)} µs`;
  if (ms < 1000) return `${ms.toFixed(1)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

/**
 * Downsample two recordings to a shared index axis for side-by-side comparison.
 * Each series is normalised independently because the two captures can have
 * different sample counts and durations.
 */
export function alignSeries(leftValues, rightValues, samples = 200) {
  const resample = (values) => {
    const finite = (values || []).filter(Number.isFinite);
    if (finite.length === 0) return [];
    if (finite.length <= samples) return finite.slice();
    const step = (finite.length - 1) / (samples - 1);
    return Array.from({ length: samples }, (_, index) => finite[Math.round(index * step)]);
  };
  return { left: resample(leftValues), right: resample(rightValues) };
}
