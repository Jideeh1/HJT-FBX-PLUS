# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#logging + the per import diagnostics collector the ui n reports read from

from __future__ import annotations

import logging
import logging.handlers
import os
import threading
import time

LOGGER_NAME = "hjt_fbx_plus"
log = logging.getLogger(LOGGER_NAME)
_file_handler = None


def setup(log_dir=None, debug=False):
    global _file_handler
    log.setLevel(logging.DEBUG if debug else logging.INFO)
    if not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("HJT FBX+ %(levelname)s: %(message)s"))
        h.setLevel(logging.WARNING)
        log.addHandler(h)
    if log_dir and _file_handler is None:
        try:
            os.makedirs(log_dir, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(os.path.join(log_dir, "hjt_fbx_plus.log"),
                                                      maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s: %(message)s"))
            log.addHandler(fh)
            _file_handler = fh
        except OSError:
            pass  #readonly config dir or w/e, console logging still works
    if _file_handler is not None:
        _file_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    return log


def teardown():
    global _file_handler
    if _file_handler is not None:
        log.removeHandler(_file_handler)
        _file_handler.close()
        _file_handler = None


def log_path():
    return getattr(_file_handler, "baseFilename", "") if _file_handler else ""


LEVELS = ("INFO", "WARNING", "ERROR")


class Diagnostics:
    #messages for one import run, grouped per model
    def __init__(self):
        self.messages = []
        self._lock = threading.Lock()
        self.started = time.time()

    def add(self, level, text, model="", code=""):
        level = level.upper()
        if level not in LEVELS:
            level = "INFO"
        with self._lock:
            self.messages.append({"level": level, "text": text, "model": model, "code": code})
        if level == "ERROR":
            log.error("%s%s", ("[%s] " % model) if model else "", text)
        elif level == "WARNING":
            log.warning("%s%s", ("[%s] " % model) if model else "", text)
        else:
            log.info("%s%s", ("[%s] " % model) if model else "", text)

    def info(self, text, model="", code=""):
        self.add("INFO", text, model, code)

    def warning(self, text, model="", code=""):
        self.add("WARNING", text, model, code)

    def error(self, text, model="", code=""):
        self.add("ERROR", text, model, code)

    def counts(self):
        out = {k: 0 for k in LEVELS}
        for m in self.messages:
            out[m["level"]] += 1
        return out
