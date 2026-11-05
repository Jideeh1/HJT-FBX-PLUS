# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#register / unregister in the right order. props first, panels last, state in between

from __future__ import annotations

import bpy
from bpy.app.handlers import persistent

from . import state
from .ui import lists, menus, panels, prefs, props, sync
from .ui import operators


def _classes():
    return (list(props.CLASSES) + list(prefs.CLASSES) + list(operators.CLASSES) + list(lists.CLASSES) +
            list(panels.CLASSES) + list(menus.CLASSES))


@persistent
def _load_post(_dummy):
    sync.on_load_post()


def register():
    for cls in _classes():
        bpy.utils.register_class(cls)
    props.register_props()
    state.init()
    bpy.types.TOPBAR_MT_file_import.append(menus.menu_import)
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)
    try:
        from .ui.operators.diag_ops import install_builtin_presets
        install_builtin_presets()
    except Exception:  # noqa: BLE001
        pass
    #window manager props exist now, fill history + profile lists on the first tick
    bpy.app.timers.register(lambda: (sync.on_load_post(), None)[1], first_interval=0.3)


def unregister():
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    try:
        bpy.types.TOPBAR_MT_file_import.remove(menus.menu_import)
    except (ValueError, AttributeError):
        pass
    state.shutdown()
    props.unregister_props()
    for cls in reversed(_classes()):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
