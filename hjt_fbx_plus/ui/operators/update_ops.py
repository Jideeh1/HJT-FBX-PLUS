# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#buttons for the updater. the real work is in blender/updater.py

from __future__ import annotations

import bpy

from ...blender import updater


def _prefs():
    from ... import state
    return state.prefs()


class HJTFBX_OT_update_check(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.update_check"
    bl_label = "Check for Updates"
    bl_description = "Ask GitHub for the newest HJT FBX+ release"

    def execute(self, context):
        p = _prefs()
        if not updater.check(prerelease=bool(getattr(p, "update_prerelease", False))):
            if not updater.online_allowed():
                self.report({"WARNING"}, "Online access is disabled in Preferences > System > Network")
                return {"CANCELLED"}
        return {"FINISHED"}


class HJTFBX_OT_update_install(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.update_install"
    bl_label = "Install Update"
    bl_description = "Download the new release from GitHub and install it over this version"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, title="Update HJT FBX+ to %s?" % updater.STATUS.latest,
            message="The add-on reloads itself. Restart Blender afterwards to be safe.", confirm_text="Update", icon="INFO")

    def execute(self, context):
        st = updater.STATUS
        if st.state == "READY" and st.zip_path:
            try:
                updater.install(st.zip_path)
            except Exception as exc:  # noqa: BLE001
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            self.report({"INFO"}, "HJT FBX+ updated to %s. Restart Blender to finish." % st.latest)
            return {"FINISHED"}
        if not updater.download(then_install=True):
            self.report({"WARNING"}, "Nothing to download yet, check for updates first")
            return {"CANCELLED"}
        return {"FINISHED"}


class HJTFBX_OT_update_notes(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.update_notes"
    bl_label = "Release Notes"
    bl_description = "Show what changed in the new release"

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=420)

    def draw(self, context):
        st = updater.STATUS
        col = self.layout.column(align=True)
        col.label(text="HJT FBX+ %s%s" % (st.latest, ("  (%s)" % st.published) if st.published else ""), icon="PACKAGE")
        col.separator(type="LINE") if _sep_line() else col.separator()
        lines = [ln.strip() for ln in (st.notes or "No notes for this release.").splitlines()]
        shown = 0
        for ln in lines:
            if not ln:
                continue
            for part in _wrap(ln.lstrip("#*- ").strip(), 64):
                col.label(text=part)
                shown += 1
            if shown > 28:
                col.label(text="...")
                break
        col.separator()
        col.operator("wm.url_open", text="Open on GitHub", icon="URL").url = updater.PAGE

    def execute(self, context):
        return {"FINISHED"}


def _sep_line():
    try:
        return "type" in bpy.types.UILayout.bl_rna.functions["separator"].parameters
    except Exception:  # noqa: BLE001
        return False


def _wrap(text, width):
    out, cur = [], ""
    for w in text.split():
        if cur and len(cur) + len(w) + 1 > width:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        out.append(cur)
    return out or [""]


CLASSES = (HJTFBX_OT_update_check, HJTFBX_OT_update_install, HJTFBX_OT_update_notes)
