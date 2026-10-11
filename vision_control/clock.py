"""A monotonic clock with the same epoch in every process.

Older CPython on macOS offsets time.monotonic() per process. CLOCK_MONOTONIC
avoids that offset. Windows' monotonic clock is already system-wide on the
supported Python versions. Never use wall time for motion deadlines.
"""
import time


def monotonic():
    if hasattr(time, 'clock_gettime') and hasattr(time, 'CLOCK_MONOTONIC'):
        return time.clock_gettime(time.CLOCK_MONOTONIC)
    return time.monotonic()
