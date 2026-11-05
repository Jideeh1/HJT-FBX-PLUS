# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#diagnostics: reports, history, fbx structure dumps, presets

from __future__ import annotations

import io
import os

import bpy
from bpy.props import StringProperty
from bl_operators.presets import AddPresetBase

from ... import state
from ...blender.report import to_text
from ...core.options import OPTIONS
from ...fbx.ascii_writer import dump_structure
from ...fbx.document import FbxDocument
from ...fbx.inspect import inspect_document
from .. import sync
from .profile_ops import _show_text

REPORT_TEXT = "HJT FBX+ Report"


def _text(name, body):
    txt = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    txt.from_string(body)
    txt.current_line_index = 0
    return txt


class HJTFBX_OT_report_open(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.report_open"
    bl_label = "Open Report"
    bl_description = "Show the full import report in the Text Editor"

    rid: StringProperty(options={"SKIP_SAVE"})

    def execute(self, context):
        st = state.get()
        d = None
        if self.rid:
            try:
                d = st.history.load(self.rid)
            except (OSError, ValueError):
                d = None
        else:
            d = st.last_report
            if d is None:
                entries = st.history.entries(1)
                d = st.history.load(entries[0]["id"]) if entries else None
        if d is None:
            self.report({"WARNING"}, "No import report yet")
            return {"CANCELLED"}
        txt = _text(REPORT_TEXT, to_text(d))
        if not _show_text(context, txt):
            self.report({"INFO"}, "Open the text '%s' in a Text Editor" % REPORT_TEXT)
        return {"FINISHED"}


class HJTFBX_OT_report_copy(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.report_copy"
    bl_label = "Copy Report"
    bl_description = "Copy the last import report to the clipboard (useful for bug reports)"

    def execute(self, context):
        st = state.get()
        if st.last_report is None:
            self.report({"WARNING"}, "No import report yet")
            return {"CANCELLED"}
        context.window_manager.clipboard = to_text(st.last_report)
        self.report({"INFO"}, "Report copied to the clipboard")
        return {"FINISHED"}


class HJTFBX_OT_history_refresh(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.history_refresh"
    bl_label = "Refresh History"
    bl_description = "Reload the import history"

    def execute(self, context):
        sync.sync_history(context.window_manager.hjt_fbx_plus, state.get())
        return {"FINISHED"}


class HJTFBX_OT_history_clear(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.history_clear"
    bl_label = "Clear History"
    bl_description = "Delete all saved import reports"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        st = state.get()
        n = st.history.clear()
        sync.sync_history(context.window_manager.hjt_fbx_plus, st)
        self.report({"INFO"}, "Deleted %d reports" % n)
        return {"FINISHED"}


class HJTFBX_OT_dump_fbx(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.dump_fbx"
    bl_label = "Show FBX Structure"
    bl_description = "Write a readable dump of the active model's FBX node tree (and its summary) to the Text Editor"

    def execute(self, context):
        st = state.get()
        s = context.window_manager.hjt_fbx_plus
        if not s.models or s.models_index >= len(s.models):
            return {"CANCELLED"}
        m = st.session.models.get(s.models[s.models_index].key)
        if m is None:
            return {"CANCELLED"}
        path = m.path
        if m.member:
            src = st.session.sources.get(m.source_key)
            try:
                reader, _a, _t = st.services.archive(m.path, src.password if src else None)
                entry = reader.find(m.member)
                path = st.cache.materialize(reader, [entry])[entry.path]
            except Exception as exc:  # noqa: BLE001
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
        try:
            with FbxDocument.open(path, lazy=False) as doc:
                out = io.StringIO()
                summ = inspect_document(doc)
                out.write("# HJT FBX+ structure dump: %s\n# FBX %d (%s), %d nodes\n" % (m.label, doc.version, doc.format, doc.node_count))
                for i in summ.get("issues", []):
                    out.write("# %s: %s\n" % (i["level"].upper(), i["message"]))
                out.write("\n")
                dump_structure(out, doc.root, array_limit=8, max_children=300)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, "Cannot read FBX: %s" % exc)
            return {"CANCELLED"}
        body = out.getvalue()
        if len(body) > 8_000_000:
            body = body[:8_000_000] + "\n... (truncated)\n"
        txt = _text("HJT FBX+ Structure: %s" % m.label[:40], body)
        if not _show_text(context, txt):
            self.report({"INFO"}, "Open the text '%s' in a Text Editor" % txt.name)
        return {"FINISHED"}


class HJTFBX_MT_presets(bpy.types.Menu):
    bl_label = "Import Presets"
    preset_subdir = "hjt_fbx_plus/import"
    preset_operator = "script.execute_preset"
    draw = bpy.types.Menu.draw_preset


class HJTFBX_OT_preset_add(AddPresetBase, bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.preset_add"
    bl_label = "Add Import Preset"
    bl_description = "Save or remove an HJT FBX+ import settings preset"
    preset_menu = "HJTFBX_MT_presets"
    preset_defines = ["s = bpy.context.scene.hjt_fbx_plus"]
    preset_values = ["s.%s" % k for k in OPTIONS] + ["s.profile"]
    preset_subdir = "hjt_fbx_plus/import"


BUILTIN_PRESETS = {
    "Faithful (Default)": {},
    "Fast Batch (C++ engine)": {"engine": "CPP", "keep_tangents": False},
    "Keep FBX Materials": {"material_mode": "KEEP"},
    "Textures to Folder": {"texture_storage": "COPY", "copy_dir": "//textures"},
}


def install_builtin_presets():
    #write a few starter presets once, users can delete/override them like any other preset
    try:
        base = bpy.utils.user_resource("SCRIPTS", path=os.path.join("presets", "hjt_fbx_plus", "import"), create=True)
    except Exception:  # noqa: BLE001
        return
    if not base or any(f.endswith(".py") for f in os.listdir(base)):
        return
    from ...core.options import defaults
    for name, over in BUILTIN_PRESETS.items():
        vals = defaults()
        vals.update(over)
        lines = ["import bpy", "s = bpy.context.scene.hjt_fbx_plus"]
        for k, v in vals.items():
            lines.append("s.%s = %r" % (k, v))
        fname = bpy.path.clean_name(name).lower() + ".py"
        with open(os.path.join(base, fname), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


CLASSES = (HJTFBX_OT_report_open, HJTFBX_OT_report_copy, HJTFBX_OT_history_refresh, HJTFBX_OT_history_clear,
           HJTFBX_OT_dump_fbx, HJTFBX_MT_presets, HJTFBX_OT_preset_add)
