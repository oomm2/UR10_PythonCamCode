import {
  JOINT_NAMES, MonitorRequestError, fetchJson, validateState, hasLiveTelemetry,
  createPoller, createEventStream, createHistory, jointDisplay,
  tcpSpeedMagnitudes, seriesPoints, seriesRange,
  parseTrajectory, trajectoryTrack, trackLength, parseLatency, latencyModeLabel,
  jointJumps, exceedsJumpThreshold, formatLatency, alignSeries, isVector6,
  seriesRange as computeRange,
} from './telemetry.js';
import { createReplay } from './replay.js';
import { createThemeController } from './theme.js';

const $ = (selector) => document.querySelector(selector);
const jointRoot = $('#joints');
const logRoot = $('#log');
const sceneStatus = $('#scene-status');
const chartJoint = $('#chart-joints');
const chartLoad = $('#chart-load');
const jumpAlert = $('#jump-alert');
const replayScrub = $('#replay-scrub');
const replayReadout = $('#replay-readout');

const JUMP_THRESHOLD_DEG = 25;
const DETAILS_POLL_MS = 1000;
const JOINT_COLOURS = ['#60a5fa', '#f472b6', '#facc15', '#4ade80', '#22d3ee', '#a78bfa'];

let robotScene = null;
let latestQ = null;
let jointLimits = null;
let lastConnection = null;
let lastControlKey = '';
let lastRobotError = '';
let lastPollError = '';
let streamMode = false;
let telemetrySource = null;
let config = { robot_model: 'UR10', latency_mode: 'monotonic' };

// Freeze mode holds the last rendered frame so a demo can point at a moment.
let frozen = false;
let frozenState = null;
let lastRenderedState = null;
let previousJoints = null;
let highlightIndex = -1;
let jumpCount = 0;
let lastJumpAt = 0;
let replay = null;
let replayPoints = [];

const jointHistory = createHistory();
const speedHistory = createHistory();
const currentHistory = createHistory();

const themeController = createThemeController();

function log(text, className = '') {
  const entry = document.createElement('div');
  entry.className = className;
  entry.textContent = `${new Date().toLocaleTimeString()}  ${text}`;
  logRoot.prepend(entry);
  while (logRoot.children.length > 40) logRoot.lastChild.remove();
}

function ageText(seconds) {
  if (!Number.isFinite(seconds)) return '—';
  return seconds < 1 ? `${Math.max(0, seconds * 1000).toFixed(0)} ms` : `${seconds.toFixed(1)} s`;
}

function durationText(seconds) {
  if (!Number.isFinite(seconds)) return '—';
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return hours ? `${hours}h ${minutes}m` : (minutes ? `${minutes}m ${secs}s` : `${secs}s`);
}

function badge(element, kind, text) {
  element.classList.remove('ok', 'waiting', 'error');
  if (kind) element.classList.add(kind);
  element.querySelector('span:last-child').textContent = text;
}

JOINT_NAMES.forEach((_, index) => {
  const row = document.createElement('div');
  row.className = 'joint';
  row.dataset.index = String(index);
  row.innerHTML = `
    <span class="joint-label">J${index + 1}</span>
    <div class="bar" aria-hidden="true"><i id="bar-${index}"></i></div>
    <span id="joint-${index}" class="joint-value">—</span>
  `;
  jointRoot.append(row);
});

function updateJoints(values) {
  JOINT_NAMES.forEach((_, index) => {
    const bar = $(`#bar-${index}`);
    const row = jointRoot.children[index];
    const value = values?.[index];
    const available = Number.isFinite(value);
    $(`#joint-${index}`).textContent = available ? `${(value * 180 / Math.PI).toFixed(1)}°` : '—';
    bar.classList.remove('out-of-range', 'jump');
    bar.style.width = '0%';
    row.removeAttribute('title');
    row.classList.toggle('highlighted', highlightIndex === index);
    row.classList.toggle('dimmed', highlightIndex !== -1 && highlightIndex !== index);
    if (!available || !jointLimits) return;
    const limit = jointLimits[index];
    const display = jointDisplay(value, limit);
    bar.style.width = `${display.width}%`;
    bar.classList.toggle('out-of-range', display.outOfRange);
    const range = `${(limit.lower * 180 / Math.PI).toFixed(0)}° to ${(limit.upper * 180 / Math.PI).toFixed(0)}°`;
    row.title = `${display.outOfRange ? 'Outside' : 'Within'} nominal display range ${range}. ${limit.note || ''}`;
  });
}

async function loadJointLimits() {
  try {
    const payload = await fetchJson('/joint_limits.json');
    if (!Array.isArray(payload?.joints) || payload.joints.length !== JOINT_NAMES.length
      || payload.joints.some((item, index) => item.id !== JOINT_NAMES[index]
        || !Number.isFinite(item.lower) || !Number.isFinite(item.upper)
        || item.lower >= 0 || item.upper <= 0)) {
      throw new Error('Invalid joint limits');
    }
    jointLimits = payload.joints;
    updateJoints(latestQ);
    const unusual = jointLimits
      .filter((item) => Math.abs(item.upper) < Math.PI * 2 - 1e-6)
      .map((item) => item.label || item.id);
    const parts = ['Bars show nominal ROS planning ranges.'];
    if (unusual.length) parts.push(`Reduced range: ${unusual.join(', ')}.`);
    parts.push('Amber = outside nominal range. Live values and 3D poses are never clamped.');
    $('#joint-range-note').textContent = parts.join(' ');
  } catch (error) {
    $('#joint-range-note').textContent = 'Range metadata unavailable; bars disabled. Numeric joint values remain available.';
    log(`Joint range metadata unavailable: ${error.message}`, 'warn');
  }
}

const robotModes = {
  0: 'NO_CONTROLLER', 1: 'DISCONNECTED', 2: 'CONFIRM_SAFETY', 3: 'BOOTING',
  4: 'POWER_OFF', 5: 'POWER_ON', 6: 'IDLE', 7: 'BACKDRIVE', 8: 'RUNNING',
  9: 'UPDATING_FIRMWARE', 10: 'PAUSING', 11: 'PAUSED',
};
const safetyModes = {
  1: 'NORMAL', 2: 'REDUCED', 3: 'PROTECTIVE_STOP', 4: 'RECOVERY', 5: 'SAFEGUARD_STOP',
  6: 'SYSTEM_EMERGENCY_STOP', 7: 'ROBOT_EMERGENCY_STOP', 8: 'VIOLATION', 9: 'FAULT',
  10: 'VALIDATE_JOINTS', 11: 'UNDEFINED', 12: 'BOOTING', 13: 'SCALING', 14: 'PLAIN',
};

/** Severity classes let a protective stop look different from a normal state. */
export function safetySeverity(code) {
  if (code === 1) return '';
  if (code === 2 || code === 13 || code === 14) return 'warn';
  if (code === 3 || code === 5 || code === 4) return 'warn-strong';
  if (code === 6 || code === 7 || code === 8 || code === 9) return 'bad';
  return '';
}

export function modeSeverity(code) {
  if (code === 8) return 'ok';
  if (code === 3 || code === 6 || code === 10 || code === 11) return 'warn';
  return '';
}

function updateControl(control) {
  badge($('#control-status'), control.active ? 'ok' : 'waiting',
    control.active ? 'Active' : (control.reported ? 'Stale' : 'Waiting'));
  $('#control-ip').textContent = control.reported ? control.ip || '—' : 'Unknown';
  $('#control-name').textContent = control.reported ? control.name || '—' : '—';
  $('#control-state').textContent = control.active
    ? control.state || 'controlling' : (control.reported ? 'Heartbeat stale' : 'Unknown');
  $('#control-protocol').textContent = control.reported ? control.protocol || '—' : '—';
  $('#control-age').textContent = ageText(control.age_seconds);
  $('#control-robot').textContent = control.reported ? control.robot_ip || '—' : '—';
  $('#control-count').textContent = control.reported ? String(control.heartbeats ?? '—') : '—';

  // Structured Vision telemetry: falls back to em dashes for a legacy client.
  const vision = control.telemetry || {};
  $('#vision-fps').textContent = Number.isFinite(vision.fps) ? `${vision.fps.toFixed(1)} fps` : '—';
  $('#vision-confidence').textContent = Number.isFinite(vision.confidence)
    ? `${(vision.confidence * 100).toFixed(1)}%` : '—';
  $('#vision-gesture').textContent = vision.gesture || '—';
  $('#vision-dropped').textContent = Number.isFinite(vision.dropped_frames)
    ? String(Math.round(vision.dropped_frames)) : '—';
  $('#vision-inference').textContent = Number.isFinite(vision.inference_ms)
    ? `${vision.inference_ms.toFixed(1)} ms` : '—';

  const key = control.active ? `${control.ip}|${control.name}|${control.state}|${control.protocol}` : '';
  if (key && key !== lastControlKey) {
    log(`Control heartbeat: ${control.name || 'controller'} @ ${control.ip} (${control.state || 'controlling'})`);
  } else if (!key && lastControlKey) {
    log('Controller heartbeat is stale', 'warn');
  }
  lastControlKey = key;
}

function updateLatency(latency) {
  const stats = parseLatency(latency);
  $('#latency-mode').textContent = latencyModeLabel(stats?.mode || config.latency_mode);
  $('#latency-last').textContent = formatLatency(stats?.last_ms);
  $('#latency-mean').textContent = formatLatency(stats?.mean_ms);
  $('#latency-p95').textContent = formatLatency(stats?.p95_ms);
  $('#latency-range').textContent = stats && Number.isFinite(stats.min_ms) && Number.isFinite(stats.max_ms)
    ? `${formatLatency(stats.min_ms)} / ${formatLatency(stats.max_ms)}` : '—';
  $('#latency-samples').textContent = stats ? `${stats.samples ?? 0} (${stats.rejected ?? 0} rejected)` : '—';
  if (stats?.mode === 'clock_sync') {
    const drift = stats.offset_drift_seconds;
    $('#latency-note').textContent = Number.isFinite(drift)
      ? `Clock-synced mode. Last offset vs Mac clock: ${stats.offset_seconds}s (drift ${drift}s).`
      : 'Clock-synced mode: heartbeat timestamps are compared directly against this host clock.';
  } else {
    $('#latency-note').textContent = 'Monotonic mode measures arrival delay without needing clock synchronisation with the Mac.';
  }
}

function updateDiagnostics(payload) {
  const diagnostics = payload?.diagnostics || {};
  $('#diag-reconnects').textContent = Number.isFinite(diagnostics.reconnects) ? String(diagnostics.reconnects) : '—';
  $('#diag-uptime').textContent = durationText(diagnostics.cumulative_uptime_seconds);
  $('#diag-samples').textContent = Number.isFinite(diagnostics.samples_read)
    ? diagnostics.samples_read.toLocaleString() : '—';
  const trajectory = payload?.trajectory || {};
  $('#diag-points').textContent = Number.isFinite(trajectory.points)
    ? `${trajectory.points} / ${trajectory.capacity ?? '—'}` : '—';
  const subscribers = payload?.subscribers || {};
  $('#diag-subscribers').textContent = Number.isFinite(subscribers.subscribers)
    ? String(subscribers.subscribers) : '—';
  const lastUpdate = frozenState?.last_update;
  $('#diag-sample-age').textContent = Number.isFinite(lastUpdate)
    ? ageText(Date.now() / 1000 - lastUpdate) : '—';
  $('#diag-reason').textContent = diagnostics.last_disconnect_reason
    ? `Last disconnect: ${diagnostics.last_disconnect_reason}`
    : 'No disconnect recorded in this session.';
}

function flagJump(jumps) {
  jumpCount += 1;
  lastJumpAt = Date.now();
  const worst = Math.max(...jumps);
  const worstIndex = jumps.indexOf(worst) + 1;
  jumpAlert.hidden = false;
  jumpAlert.textContent = `⚠ J${worstIndex} jumped ${(worst * 180 / Math.PI).toFixed(1)}° in one sample `
    + `(threshold ${JUMP_THRESHOLD_DEG}°, ${jumpCount} total). Possible RTDE packet loss.`;
  JOINT_NAMES.forEach((_, index) => {
    if (jumps[index] * 180 / Math.PI > JUMP_THRESHOLD_DEG) $(`#bar-${index}`).classList.add('jump');
  });
  log(`Joint jump detected: J${worstIndex} moved ${(worst * 180 / Math.PI).toFixed(1)}° in one sample`, 'bad');
}

function checkJumps(values) {
  if (!isVector6(values)) {
    previousJoints = null;
    return;
  }
  const jumps = jointJumps(previousJoints, values);
  previousJoints = values.slice();
  if (jumps && exceedsJumpThreshold(jumps, JUMP_THRESHOLD_DEG)) flagJump(jumps);
}

function renderState(state) {
  const rendered = frozen ? (frozenState || state) : state;
  if (!frozen) frozenState = state;
  if (lastPollError) log(streamMode ? 'Monitor data resumed' : 'Monitor API recovered');
  lastPollError = '';
  badge($('#connection'), rendered.connected ? 'ok' : 'waiting',
    rendered.connected ? 'Connected' : 'Disconnected');
  if (rendered.connected !== lastConnection) {
    log(rendered.connected ? 'RTDE connected' : 'RTDE disconnected', rendered.connected ? '' : 'bad');
    lastConnection = rendered.connected;
  }
  const live = hasLiveTelemetry(rendered);
  $('#ip').textContent = rendered.robot_ip || '—';
  $('#monitor-ip').textContent = rendered.monitor_ip || '—';
  $('#age').textContent = rendered.last_update === null ? '—' : ageText(Date.now() / 1000 - rendered.last_update);
  const transportText = frozen ? `Frozen · ${streamMode ? 'SSE' : 'polling'}`
    : (streamMode ? 'SSE stream' : 'HTTP polling');
  $('#transport').querySelector('span:last-child').textContent = transportText;
  $('#transport').classList.remove('waiting', 'ok', 'error');
  $('#transport').classList.add(streamMode ? 'ok' : 'waiting');
  $('#transport-metric').textContent = transportText;
  $('#frozen').hidden = !frozen;

  const robotMode = $('#robot-mode');
  robotMode.textContent = live ? robotModes[rendered.robot_mode] ?? rendered.robot_mode ?? '—' : '—';
  robotMode.className = `value ${live ? modeSeverity(rendered.robot_mode) : ''}`;
  const safety = $('#safety-mode');
  safety.textContent = live ? safetyModes[rendered.safety_mode] ?? rendered.safety_mode ?? '—' : '—';
  safety.className = `value ${live ? safetySeverity(rendered.safety_mode) : ''}`;
  $('#speed').textContent = live && Number.isFinite(rendered.speed_scaling)
    ? `${(rendered.speed_scaling * 100).toFixed(0)}%` : '—';

  latestQ = live ? rendered.actual_q : null;
  updateJoints(latestQ);
  robotScene?.update(latestQ);
  updateTcp(live ? rendered.actual_TCP_pose : null);
  updateLoad(live ? rendered : null);
  updateCharts(rendered, live);
  updateControl(rendered.control_client);
  updateLatency(rendered.latency);
  if (!frozen) checkJumps(live ? rendered.actual_q : null);
  if (rendered.error && !rendered.connected) {
    if (rendered.error !== lastRobotError) log(rendered.error, 'bad');
    lastRobotError = rendered.error;
  } else {
    lastRobotError = '';
  }
}

function updateTcp(pose) {
  if (!pose) {
    ['#tcp-position', '#tcp-orientation', '#tcp-speed'].forEach((id) => { $(id).textContent = '—'; });
    return;
  }
  $('#tcp-position').textContent = pose.slice(0, 3).map((value) => value.toFixed(3)).join(', ');
  const degrees = pose.slice(3, 6).map((value) => (value * 180 / Math.PI).toFixed(1));
  $('#tcp-orientation').textContent = `${degrees.join(', ')}°`;
}

function updateLoad(state) {
  const speed = tcpSpeedMagnitudes(state?.actual_TCP_speed);
  $('#tcp-speed').textContent = speed
    ? `${speed.linear.toFixed(3)} / ${speed.angular.toFixed(3)} m/s · rad/s` : '—';
  const current = state?.actual_joint_current;
  $('#joint-current').textContent = Array.isArray(current)
    ? current.map((value) => value.toFixed(2)).join(', ') : '—';
  const temperature = state?.joint_temperatures;
  $('#joint-temp').textContent = Array.isArray(temperature)
    ? temperature.map((value) => value.toFixed(1)).join(', ') : '—';
  const hottest = Math.max(...(temperature || []).filter(Number.isFinite), Number.NEGATIVE_INFINITY);
  const temp = $('#joint-temp');
  temp.className = `value small-value ${hottest >= 70 ? 'bad' : hottest >= 60 ? 'warn' : ''}`;
  const digitalIn = state?.actual_digital_input_bits;
  const digitalOut = state?.actual_digital_output_bits;
  $('#digital-io').textContent = Number.isFinite(digitalIn) || Number.isFinite(digitalOut)
    ? `in 0x${(digitalIn ?? 0).toString(16)} · out 0x${(digitalOut ?? 0).toString(16)}` : '—';
}

function updateCharts(state, live) {
  if (!live || frozen) return;
  jointHistory.push(state.actual_q.slice());
  const speed = tcpSpeedMagnitudes(state.actual_TCP_speed);
  if (speed) speedHistory.push(speed.linear);
  if (isVector6(state.actual_joint_current)) currentHistory.push([...state.actual_joint_current]);
  drawJointChart();
  drawLoadChart();
}

function isVector(value) {
  return isVector6(value);
}

function svgPolyline(points, stroke, width = 1.6) {
  return points ? `<polyline fill="none" stroke="${stroke}" stroke-width="${width}" stroke-linejoin="round" points="${points}"/>` : '';
}

function chartPlaceholder(svg, width, height) {
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.innerHTML = `<text x="50%" y="50%" fill="var(--muted)" font-size="11" text-anchor="middle">Waiting for telemetry…</text>`;
}

function drawJointChart() {
  const box = chartJoint.getBoundingClientRect();
  const width = Math.max(120, box.width);
  const height = Math.max(80, box.height);
  const samples = jointHistory.toArray();
  if (samples.length < 2) {
    chartPlaceholder(chartJoint, width, height);
    return;
  }
  const degrees = (samples[0] || []).map((_, index) => samples.map((sample) => sample[index] * 180 / Math.PI));
  const range = seriesRange(degrees.flat().filter(Number.isFinite), 30);
  const paths = degrees.map((series, index) => svgPolyline(
    seriesPoints(series, { width, height, ...range }), JOINT_COLOURS[index],
  )).join('');
  chartJoint.setAttribute('viewBox', `0 0 ${width} ${height}`);
  chartJoint.innerHTML = `${rangeGuides(range, width, height)}${paths}`;
}

function drawLoadChart() {
  const box = chartLoad.getBoundingClientRect();
  const width = Math.max(120, box.width);
  const height = Math.max(80, box.height);
  const speeds = speedHistory.toArray();
  if (speeds.length < 2) {
    chartPlaceholder(chartLoad, width, height);
    return;
  }
  const range = { minimum: 0, maximum: Math.max(0.05, Math.max(...speeds) * 1.15) };
  const paths = svgPolyline(seriesPoints(speeds, { width, height, ...range }), '#22d3ee');
  const currents = JOINT_NAMES.map((_, index) => currentHistory.toArray().map((sample) => sample[index]));
  const currentRange = seriesRange(currents.flat().filter(Number.isFinite), 4);
  const currentPaths = currents.map((series, index) => svgPolyline(
    seriesPoints(series, { width, height, ...currentRange }), JOINT_COLOURS[index], 1,
  )).join('');
  chartLoad.setAttribute('viewBox', `0 0 ${width} ${height}`);
  chartLoad.innerHTML = `${rangeGuides(range, width, height)}${paths}${currentPaths}`;
}

function rangeGuides(range, width, height) {
  const mid = height / 2;
  return `<line x1="0" y1="${mid}" x2="${width}" y2="${mid}" stroke="var(--chart-guide)" stroke-width="0.6" stroke-dasharray="3 4"/>`
    + `<text x="4" y="10" fill="var(--chart-axis)" font-size="9">${range.maximum.toFixed(1)}</text>`
    + `<text x="4" y="${height - 3}" fill="var(--chart-axis)" font-size="9">${range.minimum.toFixed(1)}</text>`;
}

// --- Freeze mode ---------------------------------------------------------
function toggleFreeze() {
  frozen = !frozen;
  if (frozen) {
    frozenState = lastRenderedState || frozenState;
    log('View frozen — live updates continue in the background', 'warn');
  } else {
    log('View resumed');
  }
  $('#frozen').hidden = !frozen;
  if (frozenState) renderState(frozenState);
}

// --- Trail + replay ------------------------------------------------------
function applyTrail(points) {
  replayPoints = points || [];
  const track = trajectoryTrack(replayPoints);
  const drawn = robotScene?.setTrail(track, { visible: trailEnabled }) ?? 0;
  robotScene?.setReplayMarker(null);
  updateReplayReadout(0, replayPoints.length);
  replayScrub.value = '0';
  if (replay) replay.destroy();
  replay = createReplay(replayPoints, {
    speed: Number(replaySpeed.value) || 1,
    onFrame: (point, index, total) => {
      replayScrub.value = String(Math.round((index / Math.max(1, total - 1)) * 1000));
      updateReplayReadout(index, total);
      robotScene?.setReplayMarker([point[0], point[1], point[2]]);
      if (isVector6(point.slice(3, 9))) {
        const values = point.slice(3, 9);
        updateJoints(values);
        robotScene?.update(values);
      }
    },
    onEnd: () => {
      $('#replay-play').textContent = 'Play';
      log('Trajectory replay finished');
    },
  });
  return { drawn, length: trackLength(track) };
}

function updateReplayReadout(index, total) {
  replayReadout.textContent = `${index} / ${Math.max(0, total - 1)} points`;
  if (total > 1) {
    replayReadout.title = `Path length ${trackLength(trajectoryTrack(replayPoints)).toFixed(3)} m`;
  }
}

const replaySpeed = $('#replay-speed');
const trailToggle = $('#trail-toggle');
let trailEnabled = true;

async function refreshTrajectory({ announce = true } = {}) {
  try {
    const payload = parseTrajectory(await fetchJson('/api/trajectory?limit=20000'));
    const { drawn, length } = applyTrail(payload.points);
    if (announce) log(`Trajectory loaded: ${drawn} points, ${length.toFixed(3)} m path`);
  } catch (error) {
    if (announce) log(`Trajectory unavailable: ${error.message}`, 'warn');
  }
}

async function clearTrajectory() {
  // Clearing the server buffer needs the heartbeat secret, which the dashboard
  // deliberately does not hold. Drop the local trail and replay; the server copy
  // keeps growing until its ring buffer wraps or an operator clears it.
  robotScene?.clearTrail();
  replayPoints = [];
  replay?.destroy();
  replay = null;
  updateReplayReadout(0, 0);
  replayScrub.value = '0';
  log('On-screen trail cleared (server-side buffer unchanged)');
}

// --- Baseline comparison -------------------------------------------------
async function loadBaseline() {
  const left = $('#baseline-left').value;
  const right = $('#baseline-right').value;
  if (!left || !right) {
    $('#baseline-note').textContent = 'Two recordings are needed. Record a run first (recording controls live in the API).';
    return;
  }
  try {
    const payload = await fetchJson(`/api/baseline?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}&limit=4000`);
    if (!payload.ok) throw new Error(payload.error || 'Baseline comparison failed');
    const leftJoints = payload.left.rows.map((row) => row.actual_q1 * 180 / Math.PI);
    const rightJoints = payload.right.rows.map((row) => row.actual_q1 * 180 / Math.PI);
    const aligned = alignSeries(leftJoints, rightJoints, 300);
    drawBaselineChart($('#baseline-left-chart'), aligned.left, '#60a5fa');
    drawBaselineChart($('#baseline-right-chart'), aligned.right, '#f472b6');
    $('#baseline-note').textContent = `Comparing ${payload.left.rows.length} vs ${payload.right.rows.length} rows (J1 shown). `
      + `Left path ${trackLength(payload.left.rows.map((row) => [row.tcp_x, row.tcp_y, row.tcp_z])).toFixed(3)} m, `
      + `right ${trackLength(payload.right.rows.map((row) => [row.tcp_x, row.tcp_y, row.tcp_z])).toFixed(3)} m.`;
    log(`Baseline loaded: ${left} vs ${right}`);
  } catch (error) {
    $('#baseline-note').textContent = `Baseline comparison failed: ${error.message}`;
    log(`Baseline comparison failed: ${error.message}`, 'warn');
  }
}

function drawBaselineChart(svg, values, colour) {
  const box = svg.getBoundingClientRect();
  const width = Math.max(120, box.width);
  const height = Math.max(70, box.height);
  if (!values || values.length < 2) {
    chartPlaceholder(svg, width, height);
    return;
  }
  const range = computeRange(values, 30);
  const points = seriesPoints(values, { width, height, ...range });
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.innerHTML = `${rangeGuides(range, width, height)}${svgPolyline(points, colour, 1.4)}`;
}

async function refreshRecordingList() {
  try {
    const payload = await fetchJson('/api/config');
    config = { ...config, ...payload };
    if (payload.robot_model) {
      $('#app-title').textContent = `${payload.robot_model} Monitor`;
    }
    const names = Array.isArray(payload.recordings) ? payload.recordings : [];
    [['#baseline-left', 0], ['#baseline-right', 1]].forEach(([selector, offset]) => {
      const select = $(selector);
      const previous = select.value;
      select.innerHTML = '';
      if (!names.length) {
        select.append(new Option('no recordings', ''));
      }
      names.forEach((name) => select.append(new Option(name, name)));
      if (names[offset]) select.value = names[offset];
      else if (previous && names.includes(previous)) select.value = previous;
    });
  } catch (error) {
    log(`Monitor config unavailable: ${error.message}`, 'warn');
  }
}

// --- Snapshot / report export --------------------------------------------
function buildReportHtml(imageDataUrl) {
  const rows = [
    ['Robot IP', $('#ip').textContent], ['Monitor IP', $('#monitor-ip').textContent],
    ['Connected', $('#connection').textContent.trim()], ['Robot mode', $('#robot-mode').textContent],
    ['Safety mode', $('#safety-mode').textContent], ['Speed scaling', $('#speed').textContent],
    ['TCP position', $('#tcp-position').textContent], ['TCP orientation', $('#tcp-orientation').textContent],
    ['Joint angles', JOINT_NAMES.map((_, index) => `J${index + 1}=${$(`#joint-${index}`).textContent}`).join(' · ')],
    ['Joint current', $('#joint-current').textContent], ['Joint temperature', $('#joint-temp').textContent],
    ['Control client', `${$('#control-name').textContent} @ ${$('#control-ip').textContent}`],
    ['Vision FPS', $('#vision-fps').textContent], ['Confidence', $('#vision-confidence').textContent],
    ['Gesture', $('#vision-gesture').textContent], ['Dropped frames', $('#vision-dropped').textContent],
    ['Latency mode', $('#latency-mode').textContent], ['Latency last', $('#latency-last').textContent],
    ['Latency mean / p95', `${$('#latency-mean').textContent} / ${$('#latency-p95').textContent}`],
    ['Reconnects', $('#diag-reconnects').textContent], ['Uptime', $('#diag-uptime').textContent],
    ['Trail points', $('#diag-points').textContent],
  ];
  const table = rows.map(([label, value]) => `<tr><th>${label}</th><td>${value}</td></tr>`).join('');
  return `<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8" />
<title>UR Monitor Report · ${new Date().toLocaleString()}</title>
<style>
  body { font-family: Segoe UI, system-ui, sans-serif; margin: 32px; color: #111827; }
  h1 { font-size: 22px; } .when { color: #6b7280; font-size: 13px; margin-bottom: 18px; }
  img { max-width: 100%; border: 1px solid #d1d5db; border-radius: 10px; }
  table { border-collapse: collapse; margin-top: 18px; width: 100%; }
  th, td { border: 1px solid #e5e7eb; padding: 7px 10px; text-align: left; font-size: 13px; vertical-align: top; }
  th { background: #f3f4f6; width: 220px; }
  footer { margin-top: 22px; color: #6b7280; font-size: 12px; }
</style></head>
<body>
  <h1>UR Read-only Monitor Report</h1>
  <div class="when">Captured ${new Date().toLocaleString()} · read-only RTDE telemetry (no input recipe, no URScript)</div>
  ${imageDataUrl ? `<img src="${imageDataUrl}" alt="Digital twin snapshot" />` : ''}
  <table>${table}</table>
  <footer>Generated locally by the monitor dashboard. This report contains no robot commands.</footer>
</body></html>`;
}

function downloadBlob(filename, content, type) {
  const blob = content instanceof Blob ? content : new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

function exportSnapshot() {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  let image = '';
  try {
    image = robotScene?.snapshotImage() || '';
  } catch (error) {
    log(`3D snapshot unavailable: ${error.message}`, 'warn');
  }
  if (image) {
    const base64 = image.split(',')[1] || '';
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    downloadBlob(`ur-monitor-${stamp}.png`, new Blob([bytes], { type: 'image/png' }));
  }
  downloadBlob(`ur-monitor-report-${stamp}.html`, buildReportHtml(image), 'text/html;charset=utf-8');
  log(`Export written: ur-monitor-${stamp}.png + report HTML`);
}

// --- Keyboard shortcuts --------------------------------------------------
const SHORTCUT_HANDLED = new Set([' ', 's', 'l', 'j', 'p', 'c', 't', 'r', 'g', '?', '1', '2', '3', '4', '5', '6']);

function onKeyDown(event) {
  const target = event.target;
  if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement
    || target instanceof HTMLSelectElement || target?.isContentEditable) return;
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  if (!SHORTCUT_HANDLED.has(key)) return;
  event.preventDefault();
  if (key === ' ') toggleFreeze();
  else if (key === 's') exportSnapshot();
  else if (key === 'l') toggleTrail();
  else if (key === 'j') toggleRings();
  else if (key === 'p') toggleReplay();
  else if (key === 'c') clearTrajectory();
  else if (key === 't') applyTheme(themeController.toggle());
  else if (key === 'r') { robotScene?.resetView(); log('3D view reset'); }
  else if (key === 'g') log(`Reference grid ${robotScene?.toggleGrid() ? 'shown' : 'hidden'}`);
  else if (key === '?') { $('#shortcut-card').hidden = !$('#shortcut-card').hidden; }
  else if (/^[1-6]$/.test(key)) selectJoint(Number(key) - 1);
}

function selectJoint(index) {
  highlightIndex = highlightIndex === index ? -1 : index;
  robotScene?.highlightJoint(highlightIndex);
  updateJoints(latestQ);
  log(highlightIndex === -1 ? 'Joint highlight cleared' : `Highlighting J${highlightIndex + 1}`);
}

function toggleTrail() {
  trailEnabled = !trailEnabled;
  trailToggle.classList.toggle('active', trailEnabled);
  const track = trajectoryTrack(replayPoints);
  robotScene?.setTrail(track, { visible: trailEnabled });
  log(`Trajectory trail ${trailEnabled ? 'shown' : 'hidden'}`);
}

function toggleRings() {
  const next = !$('#rings-toggle').classList.contains('active');
  robotScene?.setRingsVisible(next);
  $('#rings-toggle').classList.toggle('active', next);
  log(`Joint limit rings ${next ? 'shown' : 'hidden'}`);
}

function toggleReplay() {
  if (!replay) {
    log('No trajectory loaded to replay', 'warn');
    return;
  }
  const playing = replay.toggle();
  $('#replay-play').textContent = playing ? 'Pause' : 'Play';
}

function applyTheme(theme) {
  robotScene?.setTheme(theme);
  return theme;
}

// --- Wiring --------------------------------------------------------------

$('#theme-toggle').addEventListener('click', () => {
  applyTheme(themeController.toggle());
  log(`Theme: ${themeController.theme}`);
});
$('#help-toggle').addEventListener('click', () => { $('#shortcut-card').hidden = !$('#shortcut-card').hidden; });
$('#snapshot-button').addEventListener('click', exportSnapshot);
trailToggle.addEventListener('click', toggleTrail);
$('#rings-toggle').addEventListener('click', toggleRings);
$('#replay-play').addEventListener('click', toggleReplay);
$('#replay-reset').addEventListener('click', () => { replay?.reset(); $('#replay-play').textContent = 'Play'; });
replayScrub.addEventListener('input', () => {
  if (!replay) return;
  replay.pause();
  $('#replay-play').textContent = 'Play';
  replay.seek(Number(replayScrub.value) / 1000);
});
replaySpeed.addEventListener('change', () => replay?.setSpeed(Number(replaySpeed.value)));
$('#trajectory-refresh').addEventListener('click', () => refreshTrajectory());
$('#baseline-load').addEventListener('click', loadBaseline);
window.addEventListener('keydown', onKeyDown);

const poller = createPoller({
  request: async () => validateState(await fetchJson('/api/state')),
  onState: (state) => { lastRenderedState = state; renderState(state); },
  onError: (error) => renderPollingError(error instanceof MonitorRequestError
    ? error : new MonitorRequestError('dashboard', `Dashboard update failed: ${error.message}`)),
});

function renderPollingError(error) {
  const labels = {
    network: 'Monitor offline', timeout: 'Monitor timeout',
    http: 'Monitor HTTP error', response: 'Invalid monitor data',
  };
  badge($('#connection'), 'error', labels[error.kind] || 'Dashboard error');
  badge($('#control-status'), 'waiting', 'Unknown');
  ['#control-ip', '#control-name', '#control-state', '#control-protocol', '#control-age', '#control-robot',
    '#vision-fps', '#vision-confidence', '#vision-gesture', '#vision-dropped', '#vision-inference', '#control-count']
    .forEach((id) => { $(id).textContent = '—'; });
  ['#robot-mode', '#safety-mode', '#speed', '#age', '#tcp-position', '#tcp-orientation', '#tcp-speed',
    '#joint-current', '#joint-temp', '#digital-io'].forEach((id) => { $(id).textContent = '—'; });
  latestQ = null;
  updateJoints(null);
  robotScene?.update(null);
  lastConnection = null;
  lastControlKey = '';
  const key = `${error.kind}:${error.message}`;
  if (key !== lastPollError) log(error.message, 'bad');
  lastPollError = key;
}

// Prefer the pushed stream: it removes per-frame HTTP overhead and reports at the
// RTDE rate instead of the polling interval. Polling stays as the fallback.
telemetrySource = createEventStream('/api/stream', {
  onState: (state) => {
    if (!streamMode) {
      streamMode = true;
      poller.stop();
      log('Live stream connected');
    }
    lastRenderedState = state;
    renderState(state);
  },
  onError: (error) => {
    if (streamMode) {
      streamMode = false;
      log('Live stream unavailable; falling back to polling', 'warn');
      poller.start();
    }
    if (!streamMode) renderPollingError(error);
  },
});

if (!telemetrySource) {
  log('Live stream unsupported; using polling', 'warn');
}
poller.start();
loadJointLimits();
// The 3D model depends on robot_model from /api/config, so the config read is
// awaited first. A failure here only costs the digital twin's model choice —
// telemetry keeps working either way.
const sceneReady = refreshRecordingList();

sceneReady.then(() => import('./scene.js')).then(({ createRobotScene }) => {
  robotScene = createRobotScene($('#scene'), sceneStatus, log, {
    robotModel: config.robot_model,
  });
  robotScene.update(latestQ);
  applyTheme(themeController.theme);
  refreshTrajectory({ announce: false });
}).catch((error) => {
  sceneStatus.textContent = '3D unavailable — telemetry remains available';
  log(`3D view unavailable: ${error.message}`, 'warn');
});

// Diagnostics, latency and the replay trail are heavier than the live tick, so
// they are polled on their own slower cadence rather than pushed every frame.
setInterval(async () => {
  if (frozen) return;
  try {
    updateDiagnostics(await fetchJson('/api/diagnostics'));
  } catch { /* the live pill already reports transport health */ }
  if (!replay?.playing) refreshTrajectory({ announce: false });
}, DETAILS_POLL_MS);
