# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#addon preferences: cache, external tools, optional packages, performance, logging

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from .. import state


def _reconfigure(self, context):
    st = state.get()
    if st is None:
        return
    st.cache.configure(root=bpy.path.abspath(self.cache_dir) if self.cache_dir else None,
                       policy=self.cache_policy, max_mb=self.cache_max_mb)
    st.tasks.set_workers(self.workers)
    st.services.tool_paths = state.tool_paths(self)
    st.history.keep = self.history_keep
    from ..core import log as logmod
    logmod.setup(st.log_dir, debug=self.debug_log)
    from .operators import session_ops
    session_ops._REPORT_CACHE.clear()


class HJTFBX_Prefs(bpy.types.AddonPreferences):
    bl_idname = state.PACKAGE

    cache_dir: StringProperty(name="Cache Folder", subtype="DIR_PATH", update=_reconfigure,
                              description="Where files extracted from archives are kept temporarily (empty = system temp folder)")
    cache_policy: EnumProperty(name="Clean Up", update=_reconfigure, default="AFTER_IMPORT", items=[
        ("AFTER_IMPORT", "After Each Import", "Delete extracted files as soon as an import batch finishes (textures are packed first)"),
        ("SESSION", "When Blender Closes", "Keep extracted files for this Blender session (faster re-imports)"),
        ("KEEP", "Never", "Keep extracted files until you press Clear Cache")])
    cache_max_mb: IntProperty(name="Size Limit (MB)", default=0, min=0, max=1_000_000, update=_reconfigure,
                              description="Oldest extracted archives are removed when the cache grows past this size (0 = no limit)")
    workers: IntProperty(name="Background Threads", default=2, min=1, max=8, update=_reconfigure,
                         description="Threads used for archive reading, extraction and FBX analysis")
    auto_analyze: BoolProperty(name="Analyze on Add", default=True,
                               description="Inspect models as soon as they are added to the queue")
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

    def draw(self, context):
        layout = self.layout
        st = state.get()
        col = layout.column()
        head = col.box()
        r = head.row()
        r.label(text="HJT FBX+", icon="IMPORT")
        r.label(text="Advanced FBX & Game Asset Importer  -  By HJT Studio")

        box = col.box()
        box.label(text="Temporary Cache", icon="FILE_CACHE")
        box.prop(self, "cache_dir")
        r = box.row()
        r.prop(self, "cache_policy")
        r.prop(self, "cache_max_mb")
        if st is not None:
            used, n = st.cache.usage()
            r = box.row()
            r.label(text="In use: %.1f MB in %d files" % (used / 1048576.0, n))
            r.operator("hjt_fbx_plus.cache_clear", icon="TRASH")
            r.operator("hjt_fbx_plus.open_folder", text="", icon="FILE_FOLDER").which = "CACHE"

        box = col.box()
        box.label(text="Archive Tools (RAR and exotic 7z/ZIP methods)", icon="PACKAGE")
        for k in ("tool_7z", "tool_unrar", "tool_unar", "tool_bsdtar"):
            box.prop(self, k)
        if st is not None:
            from .operators.session_ops import backend_info
            info, depstat = backend_info(st)
            sub = box.column(align=True)
            sub.label(text="ZIP: " + info["zip"])
            sub.label(text="7Z: " + info["7z"])
            sub.label(text="RAR: " + info["rar"], icon="CHECKMARK" if info["tools"] and "not available" not in info["rar"] else "ERROR")
            sub.label(text="AES decryption: " + info["aes"])
            box.operator("hjt_fbx_plus.tools_detect", icon="VIEWZOOM")

            box = col.box()
            box.label(text="Optional Python Packages", icon="SCRIPTPLUGINS")
            from ..blender.deps import OPTIONAL
            for pkg, meta in OPTIONAL.items():
                r = box.row()
                ver = depstat.get(pkg)
                r.label(text="%s: %s" % (meta["label"], ("installed " + ver) if ver else "not installed"),
                        icon="CHECKMARK" if ver else "DOT")
                if not ver:
                    r.operator("hjt_fbx_plus.install_dependency", text="Install").package = pkg
                box.label(text="    " + meta["why"])

        box = col.box()
        box.label(text="Performance & Logging", icon="PREFERENCES")
        r = box.row()
        r.prop(self, "workers")
        r.prop(self, "auto_analyze")
        r = box.row()
        r.prop(self, "debug_log")
        r.prop(self, "history_keep")
        r = box.row()
        r.operator("hjt_fbx_plus.open_folder", text="Logs", icon="TEXT").which = "LOGS"
        r.operator("hjt_fbx_plus.open_folder", text="Profiles", icon="PRESET").which = "PROFILES"
        r.operator("hjt_fbx_plus.open_folder", text="History", icon="RECOVER_LAST").which = "HISTORY"


CLASSES = (HJTFBX_Prefs,)
