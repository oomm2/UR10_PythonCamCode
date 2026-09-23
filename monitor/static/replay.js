/**
 * Trajectory replay controller.
 *
 * Turning a recorded TCP path into a scrubable timeline is what makes the
 * monitor useful *after* a demo: you can freeze on a moment, step through it and
 * point at the pose the robot actually held.
 *
 * The controller is transport-only. It never talks to the server and never
 * drives the robot; it indexes into an already-downloaded point array.
 */
export const DEFAULT_SPEEDS = [0.25, 0.5, 1, 2, 4];

export function createReplay(points, { speed = 1, onFrame, onEnd } = {}) {
  const frames = Array.isArray(points) ? points.filter((point) => Array.isArray(point)) : [];
  let index = 0;
  let playing = false;
  let rate = speed;
  let frameHandle = null;
  let lastTimestamp = null;

  const emit = () => {
    if (typeof onFrame === 'function' && frames.length) {
      onFrame(frames[index], index, frames.length);
    }
  };

  // Replay runs on requestAnimationFrame when available and falls back to a
  // timer so the pure-logic tests can inject their own scheduler.
  const schedule = typeof requestAnimationFrame === 'function'
    ? (callback) => requestAnimationFrame(callback)
    : (callback) => setTimeout(() => callback(performance.now()), 16);
  const cancel = typeof cancelAnimationFrame === 'function'
    ? (handle) => cancelAnimationFrame(handle)
    : (handle) => clearTimeout(handle);

  function step(timestamp) {
    if (!playing) return;
    if (lastTimestamp === null) lastTimestamp = timestamp;
    const elapsed = Math.max(0, timestamp - lastTimestamp);
    // One recorded frame is consumed per real frame at 1x, scaled by the rate.
    const advance = Math.max(1, Math.round((elapsed / 16.7) * rate));
    lastTimestamp = timestamp;
    index += advance;
    if (index >= frames.length - 1) {
      index = Math.max(0, frames.length - 1);
      emit();
      stop();
      if (typeof onEnd === 'function') onEnd();
      return;
    }
    emit();
    frameHandle = schedule(step);
  }

  function stop() {
    playing = false;
    lastTimestamp = null;
    if (frameHandle !== null) cancel(frameHandle);
    frameHandle = null;
  }

  return {
    get length() {
      return frames.length;
    },
    get index() {
      return index;
    },
    get playing() {
      return playing;
    },
    get speed() {
      return rate;
    },
    setSpeed(value) {
      rate = Number.isFinite(value) && value > 0 ? value : 1;
      return rate;
    },
    play() {
      if (playing || frames.length < 2) return playing;
      if (index >= frames.length - 1) index = 0;
      playing = true;
      lastTimestamp = null;
      frameHandle = schedule(step);
      return playing;
    },
    pause: stop,
    toggle() {
      return playing ? (stop(), false) : (this.play(), playing);
    },
    /** Jump to a 0..1 position without changing the play state. */
    seek(fraction) {
      if (!frames.length) return index;
      const clamped = Math.min(1, Math.max(0, Number.isFinite(fraction) ? fraction : 0));
      index = Math.round(clamped * (frames.length - 1));
      emit();
      return index;
    },
    reset() {
      stop();
      index = 0;
      emit();
    },
    destroy: stop,
  };
}
