"""Cancel-aware threading helpers for the GUI.

Tkinter is not thread-safe: every widget call must happen on the thread
that owns the mainloop. Downloads, however, are slow and blocking. So the
worker thread never touches a widget -- it pushes events onto a queue and
the Tk thread drains that queue on a timer.

Cancellation is cooperative: the engine polls ``should_stop`` between items,
and ``EventBridge.cancelled`` is available to hooks that run mid-item.
"""

import queue
import threading


class Cancelled(Exception):
    """Raised inside a worker when the user asked it to stop."""


class EventBridge:
    """Thread-safe hand-off from worker threads to the Tk main thread."""

    def __init__(self):
        self._queue = queue.Queue()
        self._cancel = threading.Event()
        self._condition = threading.Condition()
        self._paused = False

    # -- worker side --

    def emit(self, name, payload):
        self._queue.put((name, payload))

    def request_cancel(self):
        self._cancel.set()
        self.request_resume()

    def request_pause(self):
        with self._condition:
            self._paused = True

    def request_resume(self):
        with self._condition:
            self._paused = False
            self._condition.notify_all()

    def checkpoint(self):
        """Pause only the worker at existing engine checkpoints; Stop wakes it."""
        with self._condition:
            if self._paused and not self.cancelled:
                self.emit("pause_state", {"paused": True})
                while self._paused and not self.cancelled:
                    self._condition.wait()
                self.emit("pause_state", {"paused": False})
        return self.cancelled

    def reset(self):
        """Prepare for a fresh run."""
        self._cancel.clear()
        self.request_resume()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    @property
    def cancelled(self):
        return self._cancel.is_set()

    # -- UI side --

    def pump(self, handler, max_events=200):
        """Deliver queued events to ``handler``. Call from the Tk thread."""
        for _ in range(max_events):
            try:
                name, payload = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                handler(name, payload)
            except Exception:  # noqa: BLE001 - a bad handler must not kill the pump
                pass


class Worker(threading.Thread):
    """Runs one callable off the UI thread and reports its outcome."""

    def __init__(self, target, bridge):
        super().__init__(daemon=True)
        self._target = target
        self._bridge = bridge

    def run(self):
        try:
            result = self._target()
        except Cancelled:
            self._bridge.emit("worker_cancelled", {})
        except Exception as exc:  # noqa: BLE001 - reported to the UI
            self._bridge.emit("worker_error", {"error": exc})
        else:
            self._bridge.emit("worker_done", {"result": result})
