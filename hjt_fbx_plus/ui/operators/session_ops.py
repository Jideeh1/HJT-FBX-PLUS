# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#queue, archive browser, cache n dependency operators

from __future__ import annotations

import os

import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty

from ... import state
from ...archives import backend_report
from ...archives import tools as arc_tools
from ...blender import deps
from .. import sync
from ..props import snapshot


def _st(op):
    st = state.get()
    if st is None:
        op.report({"ERROR"}, "HJT FBX+ is not initialized")
    return st


class HJTFBX_OT_analyze(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.analyze"
    bl_label = "Analyze"
    bl_description = "Inspect the models again (counts, textures, issues) with the current profile and settings"

    scope: EnumProperty(items=[("ACTIVE", "Active", ""), ("SELECTED", "Checked", ""), ("ALL", "All", "")], default="SELECTED")

    def execute(self, context):
        st = _st(self)
        if st is None:
            return {"CANCELLED"}
        s = context.window_manager.hjt_fbx_plus
        keys = []
        if self.scope == "ACTIVE" and s.models and s.models_index < len(s.models):
            keys = [s.models[s.models_index].key]
        else:
            keys = [m.key for m in st.session.models.values() if self.scope == "ALL" or m.selected]
        st.session.analyze(keys, snapshot(context.scene.hjt_fbx_plus))
        return {"FINISHED"}


class HJTFBX_OT_select(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.select"
    bl_label = "Select Models"
    bl_description = "Change which models are checked for import"

    mode: EnumProperty(items=[("ALL", "All", "Check every model"), ("NONE", "None", "Uncheck every model"),
                              ("INVERT", "Invert", "Invert the checks"),
                              ("CLEAN", "Without Issues", "Check only models without warnings or errors"),
                              ("NOT_IMPORTED", "Not Imported", "Check models that were not imported yet")])

    def execute(self, context):
        st = _st(self)
        if st is None:
            return {"CANCELLED"}
        for m in st.session.models.values():
            if self.mode == "ALL":
                m.selected = True
            elif self.mode == "NONE":
                m.selected = False
            elif self.mode == "INVERT":
                m.selected = not m.selected
            elif self.mode == "CLEAN":
                m.selected = m.status == "READY"
            elif self.mode == "NOT_IMPORTED":
                m.selected = m.status != "IMPORTED"
        st.session.changed()
        sync.sync_all(context)
        return {"FINISHED"}


class HJTFBX_OT_remove(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.remove"
    bl_label = "Remove"
    bl_description = "Remove models from the queue (nothing is deleted from disk)"

    scope: EnumProperty(items=[("ACTIVE", "Active", ""), ("SELECTED", "Checked", ""), ("IMPORTED", "Imported", "")], default="ACTIVE")

    def execute(self, context):
        st = _st(self)
        if st is None:
            return {"CANCELLED"}
        s = context.window_manager.hjt_fbx_plus
        if self.scope == "ACTIVE":
            keys = [s.models[s.models_index].key] if s.models and s.models_index < len(s.models) else []
        elif self.scope == "IMPORTED":
            keys = [m.key for m in st.session.models.values() if m.status == "IMPORTED"]
        else:
            keys = [m.key for m in st.session.models.values() if m.selected]
        st.session.remove(keys)
        sync.sync_all(context)
        return {"FINISHED"}


class HJTFBX_OT_clear(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.clear"
    bl_label = "Clear Queue"
    bl_description = "Remove everything from the queue and close open archives"

    def execute(self, context):
        st = _st(self)
        if st is None:
            return {"CANCELLED"}
        st.session.clear()
        sync.sync_all(context)
        return {"FINISHED"}


class HJTFBX_OT_unlock(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.unlock"
    bl_label = "Unlock"
    bl_description = "Try the entered password on this archive"

    index: IntProperty(default=-1)

    def execute(self, context):
        st = _st(self)
        if st is None:
            return {"CANCELLED"}
        s = context.window_manager.hjt_fbx_plus
        i = self.index if self.index >= 0 else s.sources_index
        if i >= len(s.sources):
            return {"CANCELLED"}
        it = s.sources[i]
        if not it.password:
            self.report({"WARNING"}, "Enter the password first")
            return {"CANCELLED"}
        st.session.unlock(it.key, it.password, auto_analyze=state.auto_analyze(),
                          options=snapshot(context.scene.hjt_fbx_plus))
        return {"FINISHED"}


class HJTFBX_OT_tree_toggle(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.tree_toggle"
    bl_label = "Expand / Collapse"
    bl_description = "Expand or collapse this folder"

    path: StringProperty()

    def execute(self, context):
        st = _st(self)
        if st is None:
            return {"CANCELLED"}
        src = sync.active_source(context.window_manager.hjt_fbx_plus, st)
        if src is None:
            return {"CANCELLED"}
        if self.path in src.expanded:
            src.expanded.discard(self.path)
        else:
            src.expanded.add(self.path)
        sync.sync_tree(context.window_manager.hjt_fbx_plus, st)
        return {"FINISHED"}


class HJTFBX_OT_tree_expand(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.tree_expand"
    bl_label = "Expand All"
    bl_description = "Expand or collapse every folder of the archive"

    collapse: bpy.props.BoolProperty(default=False)

    def execute(self, context):
        st = _st(self)
        src = sync.active_source(context.window_manager.hjt_fbx_plus, st) if st else None
        if src is None or src.aidx is None:
            return {"CANCELLED"}
        src.expanded = {""} if self.collapse else set(src.aidx.dirs)
        sync.sync_tree(context.window_manager.hjt_fbx_plus, st)
        return {"FINISHED"}


class HJTFBX_OT_tree_queue(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.tree_focus_model"
    bl_label = "Show in Queue"
    bl_description = "Select this FBX in the import queue"

    path: StringProperty()

    def execute(self, context):
        st = _st(self)
        s = context.window_manager.hjt_fbx_plus
        src = sync.active_source(s, st) if st else None
        if src is None:
            return {"CANCELLED"}
        key = "%s|%s" % (src.key, self.path)
        for i, it in enumerate(s.models):
            if it.key == key:
                s.models_index = i
                return {"FINISHED"}
        return {"CANCELLED"}


class HJTFBX_OT_extract(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.extract"
    bl_label = "Extract"
    bl_description = "Extract files of the active archive to a folder of your choice"

    directory: StringProperty(subtype="DIR_PATH")
    scope: EnumProperty(items=[("ROW", "Selected Row", "The selected file, or everything under the selected folder"),
                               ("ALL", "Everything", "Every file of the archive")], default="ROW")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        st = _st(self)
        s = context.window_manager.hjt_fbx_plus
        src = sync.active_source(s, st) if st else None
        if src is None or src.aidx is None or not self.directory:
            return {"CANCELLED"}
        if self.scope == "ALL" or not s.tree or s.tree_index >= len(s.tree):
            entries = list(src.aidx.files)
        else:
            row = s.tree[s.tree_index]
            if row.is_dir:
                pre = row.path + "/"
                entries = [e for e in src.aidx.files if e.path.startswith(pre)]
            else:
                entries = [e for e in src.aidx.files if e.path == row.path]
        if not entries:
            return {"CANCELLED"}
        dest = self.directory

        def work(task):
            reader, _a, _t = st.services.archive(src.path, src.password)
            reader.extract(entries, dest, progress=lambda d, t: task.set_progress(d / max(1, t), "Extracting"),
                           cancel=task.cancel)
            return len(entries)

        def done(n):
            print("HJT FBX+: extracted %d files to %s" % (n, dest))

        def fail(exc, tb):
            print("HJT FBX+: extraction failed: %s" % exc)

        st.tasks.submit(work, key=("extract", src.key), label="Extracting %d files" % len(entries), on_done=done, on_error=fail)
        self.report({"INFO"}, "Extracting %d files in the background" % len(entries))
        return {"FINISHED"}


class HJTFBX_OT_cache_clear(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.cache_clear"
    bl_label = "Clear Cache"
    bl_description = "Delete all temporary files extracted from archives (imported textures are packed and stay)"

    def execute(self, context):
        st = _st(self)
        if st is None:
            return {"CANCELLED"}
        if st.batch is not None:
            self.report({"WARNING"}, "Wait for the running import to finish")
            return {"CANCELLED"}
        before, _n = st.cache.usage()
        st.services.close()
        st.cache.clear_all()
        self.report({"INFO"}, "Freed %.1f MB" % (before / 1048576.0))
        return {"FINISHED"}


class HJTFBX_OT_tools_detect(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.tools_detect"
    bl_label = "Detect Tools"
    bl_description = "Look for 7-Zip, UnRAR, The Unarchiver and bsdtar on this computer (needed for RAR archives)"

    def execute(self, context):
        st = _st(self)
        if st is None:
            return {"CANCELLED"}
        found = arc_tools.detect(st.services.tool_paths, refresh=True)
        if found:
            self.report({"INFO"}, "Found: " + ", ".join(t.label for t in found.values()))
        else:
            self.report({"WARNING"}, "No external archive tools found. " + arc_tools.install_hint("rar"))
        _REPORT_CACHE.clear()
        return {"FINISHED"}


_REPORT_CACHE = {}


def backend_info(st):
    #cached, detecting tools spawns processes. refreshed by Detect Tools
    if "r" not in _REPORT_CACHE:
        _REPORT_CACHE["r"] = backend_report(st.services.tool_paths)
        _REPORT_CACHE["deps"] = deps.status()
    return _REPORT_CACHE["r"], _REPORT_CACHE["deps"]


class HJTFBX_OT_install_dep(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.install_dependency"
    bl_label = "Install"
    bl_description = "Download and install this optional Python package into HJT FBX+'s own folder (needs internet)"

    package: StringProperty()

    def execute(self, context):
        st = _st(self)
        if st is None or self.package not in deps.OPTIONAL:
            return {"CANCELLED"}
        if hasattr(bpy.app, "online_access") and not bpy.app.online_access:
            self.report({"ERROR"}, "Online access is disabled. Enable it in Preferences > System > Network, or install the package manually (see the documentation).")
            return {"CANCELLED"}

        def done(pkg):
            _REPORT_CACHE.clear()
            from ...archives import crypto
            crypto._LIB = None
            print("HJT FBX+: installed %s" % pkg)

        def fail(exc, tb):
            print("HJT FBX+: install failed: %s" % exc)

        st.tasks.submit(deps.install, self.package, st.user_dir, key=("install", self.package),
                        label="Installing %s" % self.package, on_done=done, on_error=fail)
        self.report({"INFO"}, "Installing %s in the background (see the status line)" % self.package)
        return {"FINISHED"}


class HJTFBX_OT_open_folder(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.open_folder"
    bl_label = "Open Folder"
    bl_description = "Open this folder in the system file browser"

    which: EnumProperty(items=[("CACHE", "Cache", ""), ("PROFILES", "Profiles", ""), ("LOGS", "Logs", ""),
                               ("HISTORY", "History", "")])

    def execute(self, context):
        st = _st(self)
        if st is None:
            return {"CANCELLED"}
        path = {"CACHE": st.cache.root, "PROFILES": st.profiles_dir, "LOGS": st.log_dir, "HISTORY": st.history_dir}[self.which]
        os.makedirs(path, exist_ok=True)
        bpy.ops.wm.path_open(filepath=path)
        return {"FINISHED"}


CLASSES = (HJTFBX_OT_analyze, HJTFBX_OT_select, HJTFBX_OT_remove, HJTFBX_OT_clear, HJTFBX_OT_unlock,
           HJTFBX_OT_tree_toggle, HJTFBX_OT_tree_expand, HJTFBX_OT_tree_queue, HJTFBX_OT_extract,
           HJTFBX_OT_cache_clear, HJTFBX_OT_tools_detect, HJTFBX_OT_install_dep, HJTFBX_OT_open_folder)
