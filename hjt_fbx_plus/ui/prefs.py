# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#addon preferences: updates, archive tools, cache, performance, logging

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from .. import state
from ..blender import compat


def _reconfigure(self, context):
    st = state.get()
    if st is None:
        return
    st.cache.configure(root=bpy.path.abspath(self.cache_dir) if self.cache_dir else None,
                       max_mb=self.cache_max_mb)
    st.tasks.set_workers(self.workers)
    st.services.tool_paths = state.tool_paths(self)
    st.history.keep = self.history_keep
    from ..core import log as logmod
    logmod.setup(st.log_dir, debug=self.debug_log)
    from .operators import session_ops
    session_ops._REPORT_CACHE.clear()


_INTERVALS = [("0", "Every Startup", "Check each time Blender starts"),
              ("1", "Daily", "At most once a day"),
              ("7", "Weekly", "At most once a week"),
              ("30", "Monthly", "At most once a month"),
              ("-1", "Never", "Only when you press Check Now")]


class HJTFBX_Prefs(bpy.types.AddonPreferences):
    bl_idname = state.PACKAGE

    cache_dir: StringProperty(name="Cache Folder", subtype="DIR_PATH", update=_reconfigure,
                              description="Where files extracted from archives sit during an import; they are deleted when it finishes (empty = system temp folder)")
    cache_max_mb: IntProperty(name="Size Limit (MB)", default=0, min=0, max=1_000_000, update=_reconfigure,
                              description="0 = no limit")
    workers: IntProperty(name="Background Threads", default=2, min=1, max=8, update=_reconfigure,
                         description="Threads used for FBX analysis while Blender stays responsive")
    auto_analyze: BoolProperty(name="Analyze on Add", default=False, options={"HIDDEN"})
    tool_7z: StringProperty(name="7-Zip", subtype="FILE_PATH", update=_reconfigure,
                            description="7z / 7zz / 7z.exe (auto-detected when empty)")
    tool_unrar: StringProperty(name="UnRAR", subtype="FILE_PATH", update=_reconfigure,
                               description="unrar / UnRAR.exe (auto-detected when empty)")
    tool_unar: StringProperty(name="unar", subtype="FILE_PATH", update=_reconfigure,
                              description="The Unarchiver command line tool (auto-detected when empty)")
    tool_bsdtar: StringProperty(name="bsdtar", subtype="FILE_PATH", update=_reconfigure,
                                description="bsdtar / tar.exe with libarchive (auto-detected when empty)")
    debug_log: BoolProperty(name="Debug Logging", default=False, update=_reconfigure,
                            description="Write detailed messages to the log file")
    history_keep: IntProperty(name="Reports to Keep", default=50, min=0, max=1000, update=_reconfigure,
                              description="Number of import reports kept in the history")
    #updater
    update_auto: EnumProperty(name="Check for Updates", items=_INTERVALS, default="1",
                              description="How often HJT FBX+ asks GitHub for a new release")
    update_prerelease: BoolProperty(name="Include Pre-releases", default=False,
                                    description="Also offer releases marked as pre-release on GitHub")
    update_auto_install: BoolProperty(name="Install Automatically", default=False,
                                      description="Download and install new releases without asking (restart Blender after)")

    def draw(self, context):
        layout = self.layout
        st = state.get()
        from ..blender import updater
        head = layout.row()
        head.label(text="HJT FBX+  %s" % (state.addon_version() or ""), icon="OUTLINER_OB_ARMATURE")
        head.label(text="By HJT Studio")
        if compat.HAS_LAYOUT_PANEL:
            h, body = layout.panel("hjt_prefs_updates", default_closed=False)
            h.label(text="Updates", icon="URL")
            _badge(h, updater.STATUS)
        else:
            body = layout.box()
            body.label(text="Updates", icon="URL")
        if body:
            draw_update_block(body, updater, prefs=self, full=True)

        if compat.HAS_LAYOUT_PANEL:
            h, body = layout.panel("hjt_prefs_archives", default_closed=True)
            h.label(text="Archives (ZIP / 7Z / RAR)", icon="FILE_ARCHIVE")
        else:
            body = layout.box()
        if body and st is not None:
            _draw_archive_tools(body, self, st)

        if compat.HAS_LAYOUT_PANEL:
            h, body = layout.panel("hjt_prefs_perf", default_closed=True)
            h.label(text="Performance, Cache & Logs", icon="PREFERENCES")
        else:
            body = layout.box()
        if body:
            col = body.column()
            col.use_property_split = True
            col.use_property_decorate = False
            col.prop(self, "workers")
            col.prop(self, "cache_dir")
            col.prop(self, "cache_max_mb")
            if st is not None:
                used, n = st.cache.usage()
                r = col.row(align=True)
                r.label(text="In use: %.1f MB in %d files" % (used / 1048576.0, n))
                r.operator("hjt_fbx_plus.cache_clear", text="", icon="TRASH")
            col.separator()
            col.prop(self, "debug_log")
            col.prop(self, "history_keep")
            r = body.row(align=True)
            r.operator("hjt_fbx_plus.open_folder", text="Logs", icon="TEXT").which = "LOGS"
            r.operator("hjt_fbx_plus.open_folder", text="Profiles", icon="PRESET").which = "PROFILES"
            r.operator("hjt_fbx_plus.open_folder", text="History", icon="RECOVER_LAST").which = "HISTORY"


def _draw_archive_tools(layout, prefs, st):
    #zip + most 7z work out of the box. rar (n weird 7z methods) need an external tool
    from .operators.session_ops import backend_info
    info, depstat = backend_info(st)
    col = layout.column(align=True)
    col.label(text="ZIP: " + info["zip"], icon="CHECKMARK")
    col.label(text="7Z: " + info["7z"], icon="CHECKMARK")
    rar_ok = "not available" not in info["rar"]
    r = col.row()
    r.alert = not rar_ok
    r.label(text="RAR: " + info["rar"], icon="CHECKMARK" if rar_ok else "ERROR")
    col.label(text="AES decryption: " + info["aes"], icon="LOCKED")
    sub = layout.column()
    sub.use_property_split = True
    sub.use_property_decorate = False
    for k in ("tool_7z", "tool_unrar", "tool_unar", "tool_bsdtar"):
        sub.prop(prefs, k)
    layout.operator("hjt_fbx_plus.tools_detect", icon="VIEWZOOM")
    from ..blender.deps import OPTIONAL
    box = layout.box()
    box.label(text="Optional Python Packages", icon="SCRIPTPLUGINS")
    for pkg, meta in OPTIONAL.items():
        ver = depstat.get(pkg)
        r = box.row()
        r.label(text="%s: %s" % (meta["label"], ("installed " + ver) if ver else "not installed"),
                icon="CHECKMARK" if ver else "DOT")
        if not ver:
            r.operator("hjt_fbx_plus.install_dependency", text="Install").package = pkg
        d = box.row()
        d.active = False
        d.label(text="    " + meta["why"])


def _badge(layout, st):
    r = layout.row()
    r.alignment = "RIGHT"
    if st.state == "AVAILABLE":
        r.alert = True
        r.label(text="%s available" % st.latest, icon="IMPORT")
    elif st.state in ("CHECKING", "DOWNLOADING"):
        r.label(text="...", icon="SORTTIME")
    elif st.state == "UP_TO_DATE":
        r.label(text="", icon="CHECKMARK")
    elif st.state == "ERROR":
        r.label(text="", icon="ERROR")


def draw_update_block(layout, updater, prefs=None, full=False):
    #shared by prefs + the sidebar banner. reads cached status only, no network in draw obviously
    st = updater.STATUS
    col = layout.column()
    cur = updater.current_version()
    if st.state == "CHECKING":
        compat.draw_progress(col, 0.5, "Checking GitHub...", ring=True)
    elif st.state == "DOWNLOADING":
        compat.draw_progress(col, st.progress, "Downloading %s  %d%%" % (st.latest, int(st.progress * 100)))
    elif st.state == "READY":
        r = col.row()
        r.scale_y = 1.3
        r.operator("hjt_fbx_plus.update_install", text="Install %s" % st.latest, icon="CHECKMARK")
    elif st.state == "INSTALLED":
        box = col.box()
        box.alert = True
        box.label(text="Updated to %s - restart Blender to finish" % st.latest, icon="FILE_REFRESH")
    elif st.state == "AVAILABLE":
        box = col.box()
        box.label(text="%s available (you: %s)" % (st.latest, cur), icon="PACKAGE")
        r = box.row(align=True)
        r.scale_y = 1.3
        r.operator("hjt_fbx_plus.update_install", text="Update Now", icon="IMPORT")
        r.operator("hjt_fbx_plus.update_notes", text="", icon="TEXT")
    elif st.state == "UP_TO_DATE":
        col.label(text="Up to date (%s, latest release %s)" % (cur, st.latest), icon="CHECKMARK")
    elif st.state == "ERROR":
        sub = col.column(align=True)
        sub.alert = True
        sub.label(text=st.error[:90] or "Update check failed", icon="ERROR")
    else:
        col.label(text="Installed: %s" % cur, icon="INFO")
    if not full:
        return
    if not updater.online_allowed():
        col.label(text="Online access is off (Preferences > System > Network)", icon="INTERNET_OFFLINE")
    r = col.row(align=True)
    r.enabled = st.state not in ("CHECKING", "DOWNLOADING")
    r.operator("hjt_fbx_plus.update_check", text="Check Now", icon="FILE_REFRESH")
    r.operator("wm.url_open", text="", icon="URL").url = updater.PAGE
    if prefs is not None:
        sub = col.column()
        sub.use_property_split = True
        sub.use_property_decorate = False
        sub.prop(prefs, "update_auto")
        sub.prop(prefs, "update_prerelease")
        sub.prop(prefs, "update_auto_install")
    if st.checked_at:
        import time
        col.label(text="Last checked: %s" % time.strftime("%Y-%m-%d %H:%M", time.localtime(st.checked_at)), icon="TIME")


CLASSES = (HJTFBX_Prefs,)
