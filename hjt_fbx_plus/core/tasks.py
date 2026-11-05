# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#background work w/o freezing blender
# workers only ever do bpy free stuff (archive listing, extraction, fbx parsing, texture search)
# results come back through poll(), which the addon calls from a bpy.app.timers callback on the
# MAIN thread. so callbacks can touch bpy safely. never call bpy from a worker, it WILL crash someday

from __future__ import annotations

import concurrent.futures
import threading
import time
import traceback


class Task:
    __slots__ = ("key", "label", "future", "cancel", "progress", "message", "started", "on_done", "on_error",
                 "_lock")

    def __init__(self, key, label, on_done, on_error):
        self.key = key
        self.label = label
        self.future = None
        self.cancel = threading.Event()
        self.progress = 0.0
        self.message = ""
        self.started = time.monotonic()
        self.on_done = on_done
        self.on_error = on_error
        self._lock = threading.Lock()

    def set_progress(self, fraction, message=None):
        #called from the worker
        with self._lock:
            self.progress = max(0.0, min(1.0, float(fraction)))
            if message is not None:
                self.message = message

    @property
    def done(self):
        return self.future is not None and self.future.done()


class TaskManager:
    def __init__(self, workers=2):
        self.workers = max(1, int(workers))
        self._pool = None
        self._tasks = []
        self._lock = threading.Lock()

    def _executor(self):
        if self._pool is None:
            self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="hjt_fbx")
        return self._pool

    def set_workers(self, n):
        n = max(1, int(n))
        if n != self.workers:
            self.workers = n
            old = self._pool
            self._pool = None
            if old is not None:
                old.shutdown(wait=False)

    def submit(self, fn, *args, key=None, label="", on_done=None, on_error=None, **kwargs):
        #fn(task, *args, **kwargs) runs in a worker. on_done(result) / on_error(exc, tb) run on main thread
        task = Task(key, label, on_done, on_error)
        if key is not None:
            self.cancel(key)

        def run():
            return fn(task, *args, **kwargs)

        task.future = self._executor().submit(run)
        with self._lock:
            self._tasks.append(task)
        return task

    def busy(self, key=None):
        with self._lock:
            return any(not t.done and (key is None or t.key == key) for t in self._tasks)

    def active(self):
        with self._lock:
            return [t for t in self._tasks if not t.done]

    def cancel(self, key=None):
        with self._lock:
            for t in self._tasks:
                if key is None or t.key == key:
                    t.cancel.set()

    def poll(self):
        #main thread: fire callbacks of finished tasks. returns True if anything finished
        finished = []
        with self._lock:
            keep = []
            for t in self._tasks:
                (finished if t.done else keep).append(t)
            self._tasks = keep
        for t in finished:
            exc = t.future.exception()
            if exc is None:
                if t.on_done is not None and not t.cancel.is_set():
                    try:
                        t.on_done(t.future.result())
                    except Exception:  # noqa: BLE001
                        traceback.print_exc()
            elif t.on_error is not None:
                tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                try:
                    t.on_error(exc, tb)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
        return bool(finished)

    def shutdown(self):
        self.cancel()
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        with self._lock:
            self._tasks = []
