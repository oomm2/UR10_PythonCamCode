"""Camera capture and MediaPipe inference outside the Qt event loop."""
from .clock import monotonic
import math
import multiprocessing as mp
import queue
import time

MANUAL_CAMERA = 'manual'


def latest_put(channel, value):
    try:
        channel.put_nowait(value)
    except queue.Full:
        try:
            channel.get_nowait()
        except queue.Empty:
            return
        try:
            channel.put_nowait(value)
        except queue.Full:
            pass


class HandQuality:
    """Geometric/temporal validity, not a probabilistic detection confidence."""
    def __init__(self):
        self.previous = None
        self.streak = 0

    def check(self, points):
        valid = len(points) == 21 and all(
            math.isfinite(v) for p in points for v in p)
        if valid:
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            valid = (all(0 <= v <= 1 for v in xs + ys)
                     and .06 <= max(xs) - min(xs) <= .85
                     and .06 <= max(ys) - min(ys) <= .85)
        if not valid:
            self.previous, self.streak = None, 0
            return False
        palm = [points[i] for i in (0, 5, 9, 13, 17)]
        span = max(max(p[0] for p in palm) - min(p[0] for p in palm),
                   max(p[1] for p in palm) - min(p[1] for p in palm))
        center = (sum(p[0] for p in palm)/5, sum(p[1] for p in palm)/5)
        consistent = (span > .015 and (self.previous is None or
                      math.hypot(center[0]-self.previous[0], center[1]-self.previous[1]) <= .15))
        self.previous = center
        self.streak = self.streak + 1 if consistent else 0
        return self.streak >= 3


def _vision_worker(source, detection, tracking, shared, output, shutdown):
    import cv2
    import mediapipe as mp_hands
    cv2.setNumThreads(1)
    cap = None
    try:
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            raise RuntimeError('無法開啟相機或 RTSP 串流')
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        quality = HandQuality()
        with mp_hands.solutions.hands.Hands(max_num_hands=1,
                min_detection_confidence=detection, min_tracking_confidence=tracking) as hands:
            while not shutdown.is_set():
                acquired = monotonic()
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError('相機讀取失敗；控制已停止，請重新開啟相機')
                with shared.get_lock():
                    shared[:] = [1., acquired]
                frame = cv2.flip(frame, 1)
                result = hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                points, score = [], 0.0
                if result.multi_hand_landmarks:
                    hand = result.multi_hand_landmarks[0]
                    points = [(p.x,p.y,p.z) for p in hand.landmark]
                    mp_hands.solutions.drawing_utils.draw_landmarks(frame, hand, mp_hands.solutions.hands.HAND_CONNECTIONS)
                    if result.multi_handedness:
                        score = result.multi_handedness[0].classification[0].score
                valid = quality.check(points)
                latest_put(output, ('frame', acquired, frame, points, score, valid))
    except Exception as exc:
        latest_put(output, ('error', str(exc)))
    finally:
        with shared.get_lock():
            shared[0] = 0
        if cap is not None:
            cap.release()
        output.cancel_join_thread()


def _scan_worker(output):
    import cv2
    found = []
    for index in range(10):
        cap = cv2.VideoCapture(index)
        try:
            if cap.isOpened() and cap.read()[0]:
                found.append(str(index))
        finally:
            cap.release()
    output.put(found)


class CameraScanner:
    def __init__(self):
        context = mp.get_context('spawn')
        self.output = context.Queue(maxsize=1)
        self.process = context.Process(target=_scan_worker, args=(self.output,), daemon=True)
        self.started = monotonic()
        self.process.start()

    def poll(self):
        try:
            return self.output.get_nowait()
        except queue.Empty:
            if not self.process.is_alive() or monotonic() - self.started > 10:
                return []
            return None

    def close(self):
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=.2)
        self.output.close()
        self.output.cancel_join_thread()


class VisionClient:
    def __init__(self, source, detection, tracking, shared):
        context = mp.get_context('spawn')
        self.output = context.Queue(maxsize=2)
        self.shutdown = context.Event()
        self.shared = shared
        self.started = monotonic()
        self.process = context.Process(target=_vision_worker, args=(source, detection, tracking,
            shared, self.output, self.shutdown), daemon=True)
        self.process.start()

    def poll(self):
        latest = None
        while True:
            try:
                latest = self.output.get_nowait()
            except queue.Empty:
                break
        if latest is None and not self.process.is_alive():
            return ('error', '視覺 worker 已結束')
        return latest

    def close(self):
        self.shutdown.set()
        with self.shared.get_lock():
            self.shared[0] = 0
        self.process.join(timeout=.2)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=.2)
        self.output.close()
        self.output.cancel_join_thread()
