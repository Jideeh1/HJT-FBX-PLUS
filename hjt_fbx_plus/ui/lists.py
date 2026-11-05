# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#ui lists. they only draw whats on screen so 10k rows r fine

from __future__ import annotations

import bpy

STATUS_ICON = {"PENDING": "TIME", "ANALYZING": "SORTTIME", "READY": "CHECKMARK", "WARN": "ERROR", "ERROR": "CANCEL",
               "IMPORTED": "IMPORT"}
KIND_ICON = {"fbx": "FILE_3D", "image": "FILE_IMAGE", "data": "FILE_TEXT", "archive": "FILE_ARCHIVE",
             "other": "FILE", "dir": "FILE_FOLDER"}
SOURCE_ICON = {"ZIP": "FILE_ARCHIVE", "7Z": "FILE_ARCHIVE", "RAR": "FILE_ARCHIVE", "FBX": "FILE_3D", "FOLDER": "FILE_FOLDER"}
LEVEL_ICON = {"INFO": "INFO", "WARNING": "ERROR", "ERROR": "CANCEL"}


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return str(n)


class HJTFBX_UL_models(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        row = layout.row(align=True)
        row.prop(item, "selected", text="")
        row.label(text=item.label, icon=STATUS_ICON.get(item.status, "DOT"))
        sub = row.row()
        sub.alignment = "RIGHT"
        sub.enabled = False
        sub.label(text=item.profile or item.source)


class HJTFBX_UL_sources(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        row = layout.row()
        row.label(text=item.label, icon=SOURCE_ICON.get(item.kind, "FILE"))
        sub = row.row()
        sub.alignment = "RIGHT"
        if item.status == "LOCKED":
            sub.label(text="", icon="LOCKED")
        elif item.status == "ERROR":
            sub.label(text="", icon="ERROR")
        elif item.status == "LISTING":
            sub.label(text="", icon="SORTTIME")


class HJTFBX_UL_tree(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        row = layout.row(align=True)
        if item.depth:
            row.separator(factor=1.6 * item.depth)
        name = item.path.rsplit("/", 1)[-1]
        if item.is_dir:
            op = row.operator("hjt_fbx_plus.tree_toggle", text="", emboss=False,
                              icon="DISCLOSURE_TRI_DOWN" if item.expanded else "DISCLOSURE_TRI_RIGHT")
            op.path = item.path
            row.label(text=name, icon="FILE_FOLDER")
        else:
            row.label(text=name, icon=KIND_ICON.get(item.kind, "FILE"))
            if item.kind == "fbx":
                op = row.operator("hjt_fbx_plus.tree_focus_model", text="", icon="RESTRICT_SELECT_OFF", emboss=False)
                op.path = item.path
        sub = row.row()
        sub.alignment = "RIGHT"
        sub.enabled = False
        sub.label(text=item.info if item.is_dir else human_size(item.size))

    def filter_items(self, context, data, propname):
        #filtering happens in sync (much faster on huge archives), keep everything here
        return [], []


class HJTFBX_UL_messages(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        row = layout.row()
        row.label(text=item.text, icon=LEVEL_ICON.get(item.level, "DOT"))


class HJTFBX_UL_history(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        row = layout.row()
        row.label(text=item.when, icon="RECOVER_LAST")
        row.label(text=item.summary)


class HJTFBX_UL_profiles(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        row = layout.row()
        current = context.scene.hjt_fbx_plus.profile == item.pid
        row.label(text=item.name, icon="CHECKMARK" if item.builtin else "USER")
        if current:
            sub = row.row()
            sub.alignment = "RIGHT"
            sub.label(text="", icon="PINNED")


class HJTFBX_UL_slot_rules(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        row = layout.row(align=True)
        row.prop(item, "slot", text="")
        row.prop(item, "pattern", text="", icon="SORTALPHA")


class HJTFBX_UL_unity_slots(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        row = layout.row(align=True)
        row.prop(item, "key", text="", icon="NODE_TEXTURE")
        row.prop(item, "slot", text="")


class HJTFBX_UL_material_rules(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        row = layout.row(align=True)
        row.prop(item, "match", text="", icon="MATERIAL")
        row.prop(item, "category", text="")
        row.prop(item, "template", text="")


class HJTFBX_UL_categories(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        row = layout.row(align=True)
        row.prop(item, "name", text="")
        row.prop(item, "pattern", text="")
        row.prop(item, "action", text="")


CLASSES = (HJTFBX_UL_models, HJTFBX_UL_sources, HJTFBX_UL_tree, HJTFBX_UL_messages, HJTFBX_UL_history,
           HJTFBX_UL_profiles, HJTFBX_UL_slot_rules, HJTFBX_UL_unity_slots, HJTFBX_UL_material_rules,
           HJTFBX_UL_categories)
