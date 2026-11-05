# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#the one global object: profiles, cache, task pool, session, history
# created in register(), torn down in unregister(). a main thread timer pumps finished tasks

from __future__ import annotations

import atexit
import os

import bpy

from .blender import compat, deps
from .blender.pipeline import Services
from .blender.report import History
from .blender.session import Session
from .core import log as logmod
from .core.cache import CacheManager
from .core.tasks import TaskManager
from .profiles.registry import Registry

PACKAGE = __package__
_STATE = None


class AddonState:
    def __init__(self, prefs):
        self.user_dir = compat.user_dir(PACKAGE, "")
        self.profiles_dir = os.path.join(self.user_dir, "profiles")
        self.history_dir = os.path.join(self.user_dir, "history")
        self.log_dir = os.path.join(self.user_dir, "logs")
        logmod.setup(self.log_dir, debug=getattr(prefs, "debug_log", False))
        deps.activate(self.user_dir)
        self.registry = Registry(self.profiles_dir)
        self.cache = CacheManager(root=(getattr(prefs, "cache_dir", "") or None),
                                  policy=getattr(prefs, "cache_policy", "AFTER_IMPORT"),
                                  max_mb=getattr(prefs, "cache_max_mb", 0))
        try:
            self.cache.purge_stale(24.0)
        except OSError:
            pass
        self.tasks = TaskManager(getattr(prefs, "workers", 2))
        self.services = Services(self.registry, self.cache, tool_paths(prefs))
        self.session = Session(self.services, self.tasks)
        self.history = History(self.history_dir, getattr(prefs, "history_keep", 50))
        self.last_report = None
        self.batch = None          #running batch import (operator sets this)
        self.dirty = True
        self.session.on_change = self.mark_dirty

    def mark_dirty(self):
        self.dirty = True

    def shutdown(self):
        try:
            self.tasks.shutdown()
        finally:
            self.services.close()
            if self.cache.policy in ("AFTER_IMPORT", "SESSION"):
                self.cache.cleanup_session()
            logmod.teardown()


def tool_paths(prefs):
    out = {}
    for k in ("7z", "unrar", "unar", "bsdtar"):
        p = getattr(prefs, "tool_" + k, "") if prefs is not None else ""
        if p:
            out[k] = bpy.path.abspath(p)
    return out


def get():
    return _STATE


def prefs():
    addon = bpy.context.preferences.addons.get(PACKAGE)
    return addon.preferences if addon is not None else None


def addon_version():
    #legacy installs have bl_info. extensions (4.2+) dont: blender DELETES bl_info from the module
    # n goes by the manifest, found that out when the first extension install blew up on import lol
    import sys
    info = getattr(sys.modules.get(PACKAGE), "bl_info", None)
    if info and info.get("version"):
        return ".".join(str(x) for x in info["version"])
    try:
        import tomllib
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_manifest.toml"), "rb") as fh:
            return str(tomllib.load(fh).get("version", ""))
    except (ImportError, OSError, ValueError):
        return ""


def auto_analyze():
    #prefs can be None in odd setups (addon half registered, bg scripts), default to on
    return bool(getattr(prefs(), "auto_analyze", True))


def init():
    global _STATE
    if _STATE is None:
        _STATE = AddonState(prefs())
        atexit.register(_atexit)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.25, persistent=True)
    return _STATE


def shutdown():
    global _STATE
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    if _STATE is not None:
        _STATE.shutdown()
        _STATE = None


def _atexit():
    st = _STATE
    if st is not None:
        try:
            st.shutdown()
        except Exception:  # noqa: BLE001
            pass


def _tick():
    st = _STATE
    if st is None:
        return None
    finished = st.tasks.poll()
    busy = bool(st.tasks.active())
    if finished or st.dirty:
        st.dirty = False
        from .ui import sync
        try:
            sync.sync_all(bpy.context)
        except Exception:  # noqa: BLE001
            logmod.log.exception("ui sync failed")
        compat.tag_redraw_all()
    elif busy:
        compat.tag_redraw_all(("VIEW_3D",))  #progress text only
    return 0.15 if busy or st.batch is not None else 0.6
