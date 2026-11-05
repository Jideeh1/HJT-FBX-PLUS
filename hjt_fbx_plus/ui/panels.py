# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#3d view sidebar, tab "HJT FBX+"
#  Import / Game Profiles / Material Reconstruction / Advanced Settings / Archive Management / Import Diagnostics
# draw code reads cached state only, never parses or scans anything (drawing happens a LOT)

from __future__ import annotations

import bpy

from .. import state
from ..blender import compat
from ..blender.materials import META_PROP
from . import sync
from .lists import human_size

CATEGORY = "HJT FBX+"


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY

    @classmethod
    def poll(cls, context):
        return state.get() is not None and hasattr(context.window_manager, "hjt_fbx_plus")


def _settings(context):
    return context.scene.hjt_fbx_plus


def _split(layout):
    col = layout.column()
    col.use_property_split = True
    col.use_property_decorate = False
    return col


class HJTFBX_PT_import(_Base, bpy.types.Panel):
    bl_label = "Import"
    bl_order = 0

    def draw_header(self, context):
        self.layout.label(text="", icon="IMPORT")

    def draw(self, context):
        layout = self.layout
        st = state.get()
        s = context.window_manager.hjt_fbx_plus
        brand = layout.box()
        col = brand.column(align=True)
        col.label(text="HJT FBX+", icon="OUTLINER_OB_ARMATURE")
        sub = col.column(align=True)
        sub.scale_y = 0.75
        sub.label(text="Advanced FBX & Game Asset Importer")
        sub.label(text="By HJT Studio")

        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("hjt_fbx_plus.import_files", text="Add FBX / Archive", icon="FILEBROWSER")
        row.operator("hjt_fbx_plus.add_folder", text="", icon="FILE_FOLDER")
        layout.prop(_settings(context), "profile", text="Profile")

        if not s.models:
            box = layout.box()
            col = box.column(align=True)
            col.scale_y = 0.8
            col.label(text="Add .fbx files or .zip / .7z / .rar", icon="INFO")
            col.label(text="archives. Models are listed here for")
            col.label(text="preview before importing.")
            if compat.HAS_FILE_HANDLER:
                col.label(text="You can also drop files on the viewport.")
            if s.status_text:
                layout.label(text=s.status_text, icon="SORTTIME")
            return

        layout.template_list("HJTFBX_UL_models", "", s, "models", s, "models_index", rows=5)
        row = layout.row(align=True)
        row.operator_menu_enum("hjt_fbx_plus.select", "mode", text="Select", icon="CHECKBOX_HLT")
        row.operator("hjt_fbx_plus.analyze", text="", icon="FILE_REFRESH").scope = "SELECTED"
        row.operator("hjt_fbx_plus.remove", text="", icon="REMOVE").scope = "ACTIVE"
        row.operator("hjt_fbx_plus.clear", text="", icon="TRASH")

        if s.models_index < len(s.models):
            _draw_model_details(layout, st, s.models[s.models_index])

        layout.separator()
        n = sum(1 for m in s.models if m.selected)
        if s.running:
            compat.draw_progress(layout, s.progress, s.status_text or "Importing...")
            layout.label(text="Press Esc to cancel after the current step", icon="CANCEL")
        else:
            row = layout.row()
            row.scale_y = 1.6
            row.enabled = n > 0
            row.operator("hjt_fbx_plus.run", text="Import %d Model%s" % (n, "" if n == 1 else "s"), icon="IMPORT")
            if s.status_text:
                r = layout.row()
                r.label(text=s.status_text, icon="SORTTIME")
                r.operator("hjt_fbx_plus.cancel", text="", icon="X")
        if s.last_summary:
            r = layout.row()
            r.label(text=s.last_summary, icon="CHECKMARK")
            r.operator("hjt_fbx_plus.report_open", text="", icon="TEXT")


def _draw_model_details(layout, st, item):
    m = st.session.models.get(item.key)
    if m is None:
        return
    box = layout.box()
    col = box.column(align=True)
    src = st.session.sources.get(m.source_key)
    col.label(text=m.member or m.path, icon="FILE_3D")
    if src is not None and src.is_archive:
        col.label(text="in %s (%s)" % (src.label, src.backend or src.kind), icon="FILE_ARCHIVE")
    if m.status == "ANALYZING":
        col.label(text="Analyzing...", icon="SORTTIME")
        return
    if m.status == "ERROR" and not m.summary:
        for line in _wrap(m.message, 48):
            col.label(text=line, icon="CANCEL")
        return
    if not m.summary:
        col.label(text=m.message or "Not analyzed yet")
        if m.status == "PENDING":
            col.operator("hjt_fbx_plus.analyze", text="Analyze", icon="VIEWZOOM").scope = "ACTIVE"
        return
    sm = m.summary
    c = sm["counts"]
    hdr = sm.get("header", {})
    grid = box.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
    grid.label(text="Meshes: %d" % c["meshes"], icon="MESH_DATA")
    grid.label(text="Materials: %d" % c["materials"], icon="MATERIAL")
    grid.label(text="Bones: %d" % c["bones"], icon="BONE_DATA")
    grid.label(text="Animations: %d" % c["anim_stacks"], icon="ACTION")
    grid.label(text="Vertices: %s" % format(sm["geometry"]["vertices"], ","), icon="VERTEXSEL")
    grid.label(text="Shape keys: %d" % c["shape_channels"], icon="SHAPEKEY_DATA")
    col = box.column(align=True)
    col.label(text="FBX %s %d  -  %s" % (sm["format"], sm["version"], hdr.get("creator", "unknown creator")[:40]), icon="INFO")
    p = st.registry.profiles.get(m.profile_id)
    if p is not None:
        col.label(text="Profile: %s%s" % (p.name, " (auto)" if m.profile_scores else ""), icon="PRESET")
    col.label(text="Textures: %d faithful, %d inferred, %d missing" % (m.textures_faithful, m.textures_inferred, m.textures_missing),
              icon="TEXTURE" if not m.textures_missing else "ERROR")
    issues = [i for i in sm.get("issues", []) if i["level"] in ("warning", "error")]
    plan_warn = [w for pl in m.plans for w in pl.warnings][:4]
    if issues or plan_warn:
        ib = box.column(align=True)
        for i in issues[:4]:
            for k, line in enumerate(_wrap(i["message"], 52)):
                ib.label(text=line, icon="ERROR" if k == 0 else "BLANK1")
        for w in plan_warn:
            for k, line in enumerate(_wrap(w, 52)):
                ib.label(text=line, icon="TEXTURE" if k == 0 else "BLANK1")
    anims = sm.get("animations", [])
    if anims:
        box.label(text="Clips: " + ", ".join(a["name"] for a in anims[:4]) + (" ..." if len(anims) > 4 else ""), icon="ACTION")


def _wrap(text, width):
    words = (text or "").split()
    lines = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines[:6] or [""]


class HJTFBX_PT_profiles(_Base, bpy.types.Panel):
    bl_label = "Game Profiles"
    bl_order = 1
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="PRESET")

    def draw(self, context):
        layout = self.layout
        st = state.get()
        s = context.window_manager.hjt_fbx_plus
        layout.prop(_settings(context), "profile", text="Use")
        row = layout.row()
        row.template_list("HJTFBX_UL_profiles", "", s, "profiles", s, "profiles_index", rows=4)
        col = row.column(align=True)
        col.operator("hjt_fbx_plus.profile_new", text="", icon="ADD")
        col.operator("hjt_fbx_plus.profile_duplicate", text="", icon="DUPLICATE")
        col.operator("hjt_fbx_plus.profile_delete", text="", icon="REMOVE")
        col.separator()
        col.operator("hjt_fbx_plus.profile_import", text="", icon="IMPORT")
        col.operator("hjt_fbx_plus.profile_export", text="", icon="EXPORT")
        col.operator("hjt_fbx_plus.profile_reload", text="", icon="FILE_REFRESH")
        if s.profiles and s.profiles_index < len(s.profiles):
            p = st.registry.profiles.get(s.profiles[s.profiles_index].pid)
            if p is not None:
                box = layout.box()
                for k, line in enumerate(_wrap(p.description or "No description.", 50)):
                    box.label(text=line, icon="INFO" if k == 0 else "BLANK1")
                r = box.row(align=True)
                r.operator("hjt_fbx_plus.profile_use", icon="PINNED")
                r.operator("hjt_fbx_plus.profile_edit_load", text="Edit", icon="GREASEPENCIL")
        if st.registry.errors:
            layout.label(text="%d profile file(s) have problems" % len(st.registry.errors), icon="ERROR")


class HJTFBX_PT_profile_editor(_Base, bpy.types.Panel):
    bl_label = "Profile Editor"
    bl_parent_id = "HJTFBX_PT_profiles"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        st = state.get()
        pe = context.window_manager.hjt_fbx_plus.profile_edit
        if not pe.loaded_id or pe.loaded_id not in st.registry.profiles:
            layout.label(text="Select a profile and press Edit", icon="INFO")
            return
        p = st.registry.profiles[pe.loaded_id]
        if p.builtin:
            layout.label(text="Built-in: saving creates your customized copy", icon="INFO")
        col = _split(layout)
        col.prop(pe, "name")
        col.prop(pe, "description")
        col.prop(pe, "detect_min")
        box = layout.box()
        box.label(text="Materials", icon="MATERIAL")
        col = _split(box)
        for k in ("template", "alpha_mode", "base_alpha_usage", "backface_culling", "vertex_color",
                  "normal_convention", "normal_format", "shadow_tint", "shadow_threshold"):
            col.prop(pe, k)
        box.label(text="Material Rules (regex on material names)")
        _list(box, "HJTFBX_UL_material_rules", pe, "material_rules")
        box = layout.box()
        box.label(text="Textures", icon="TEXTURE")
        col = _split(box)
        col.prop(pe, "use_fbx_references")
        col.prop(pe, "use_material_json")
        col.prop(pe, "name_matching")
        box.label(text="File Name Rules (checked before the defaults)")
        _list(box, "HJTFBX_UL_slot_rules", pe, "slot_rules")
        box.label(text="Unity Material Slots")
        _list(box, "HJTFBX_UL_unity_slots", pe, "unity_slots")
        box = layout.box()
        box.label(text="Objects", icon="OUTLINER_OB_MESH")
        col = _split(box)
        col.prop(pe, "lod_action")
        col.prop(pe, "collision_action")
        col.prop(pe, "fit_bone_lengths")
        col.prop(pe, "verify_skinning")
        box.label(text="Custom Categories (regex on object names)")
        _list(box, "HJTFBX_UL_categories", pe, "categories")
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("hjt_fbx_plus.profile_edit_save", icon="FILE_TICK")
        row = layout.row(align=True)
        row.operator("hjt_fbx_plus.profile_json", icon="TEXT")
        row.operator("hjt_fbx_plus.profile_json_apply", icon="CHECKMARK")


def _list(layout, ul, data, prop):
    row = layout.row()
    row.template_list(ul, "", data, prop, data, prop + "_index", rows=3)
    col = row.column(align=True)
    op = col.operator("hjt_fbx_plus.profile_rule", text="", icon="ADD")
    op.target = prop
    op.action = "ADD"
    op = col.operator("hjt_fbx_plus.profile_rule", text="", icon="REMOVE")
    op.target = prop
    op.action = "REMOVE"


class HJTFBX_PT_materials(_Base, bpy.types.Panel):
    bl_label = "Material Reconstruction"
    bl_order = 2
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="MATERIAL")

    def draw(self, context):
        layout = self.layout
        st = _settings(context)
        col = _split(layout)
        col.prop(st, "material_mode")
        if st.material_mode == "TEMPLATE":
            col.prop_search(st, "template_material", bpy.data, "materials")
        col.separator()
        col.label(text="Texture Sources")
        col.prop(st, "use_fbx_references")
        col.prop(st, "use_material_json")
        col.prop(st, "name_matching")
        sub = col.column(align=True)
        sub.prop(st, "search_depth")
        sub.prop(st, "search_parent_levels")
        col.separator()
        col.prop(st, "texture_storage")
        if st.texture_storage == "COPY":
            col.prop(st, "copy_dir")
        col.prop(st, "reuse_images")

        box = layout.box()
        box.label(text="Tools for Selected Objects", icon="TOOL_SETTINGS")
        c = box.column(align=True)
        c.operator("hjt_fbx_plus.rebuild_materials", icon="FILE_REFRESH")
        c.operator("hjt_fbx_plus.find_missing", icon="VIEWZOOM")
        c.operator("hjt_fbx_plus.reassign_texture", text="Assign Texture to Slot...", icon="IMAGE_DATA")
        ob = context.active_object
        mat = ob.active_material if ob is not None else None
        if mat is not None:
            meta = mat.get(META_PROP)
            info = box.column(align=True)
            info.label(text=mat.name, icon="MATERIAL")
            if meta:
                info.label(text="Shading: %s" % meta.get("shading", ""))
                info.label(text="Textures: %d faithful, %d inferred" % (meta.get("textures_faithful", 0), meta.get("textures_inferred", 0)))
                if meta.get("game_shader"):
                    info.label(text="Game shader: %s" % meta["game_shader"])
                notes = meta.get("notes", "")
                for line in _wrap(notes, 52)[:4]:
                    if line:
                        info.label(text=line)
            else:
                info.label(text="Not created by HJT FBX+")


class HJTFBX_PT_advanced(_Base, bpy.types.Panel):
    bl_label = "Advanced Settings"
    bl_order = 3
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="PREFERENCES")

    def draw_header_preset(self, context):
        row = self.layout.row(align=True)
        row.menu("HJTFBX_MT_presets", text="", icon="PRESET")
        row.operator("hjt_fbx_plus.preset_add", text="", icon="ADD")
        row.operator("hjt_fbx_plus.preset_add", text="", icon="REMOVE").remove_active = True

    def draw(self, context):
        layout = self.layout
        st = _settings(context)
        col = _split(layout)
        col.prop(st, "engine")
        col.prop(st, "global_scale")
        col.prop(st, "apply_profile_import")
        box = layout.box()
        box.label(text="Geometry & Data", icon="MESH_DATA")
        c = _split(box)
        for k in ("use_custom_normals", "colors_type", "keep_tangents", "use_custom_props", "use_subsurf",
                  "split_inbetweens", "inbetween_drivers"):
            c.prop(st, k)
        box = layout.box()
        box.label(text="Scene Organization", icon="OUTLINER_COLLECTION")
        c = _split(box)
        for k in ("organize", "group_by_category", "handle_lods", "handle_collision", "tag_metadata", "stop_on_error"):
            c.prop(st, k)
        _fold(layout, st, "ui_show_armature", "Armature", "ARMATURE_DATA",
              ("fit_bones", "verify_skinning", "ignore_leaf_bones", "force_connect_children",
               "automatic_bone_orientation", "primary_bone_axis", "secondary_bone_axis", "use_prepost_rot"))
        _fold(layout, st, "ui_show_animation", "Animation", "ACTION", ("use_anim", "anim_offset"))
        _fold(layout, st, "ui_show_orientation", "Orientation", "ORIENTATION_GLOBAL",
              ("use_manual_orientation", "axis_forward", "axis_up", "bake_space_transform"))
        if st.engine == "CPP":
            layout.label(text="C++ engine ignores bone axes & manual orientation", icon="INFO")


def _fold(layout, st, prop, label, icon, keys):
    box = layout.box()
    r = box.row()
    r.prop(st, prop, text=label, icon="DISCLOSURE_TRI_DOWN" if getattr(st, prop) else "DISCLOSURE_TRI_RIGHT", emboss=False)
    r.label(text="", icon=icon)
    if getattr(st, prop):
        c = _split(box)
        for k in keys:
            c.prop(st, k)


class HJTFBX_PT_archives(_Base, bpy.types.Panel):
    bl_label = "Archive Management"
    bl_order = 4
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="FILE_ARCHIVE")

    def draw(self, context):
        layout = self.layout
        st = state.get()
        s = context.window_manager.hjt_fbx_plus
        if not s.sources:
            layout.label(text="No archives or files added yet", icon="INFO")
        else:
            layout.template_list("HJTFBX_UL_sources", "", s, "sources", s, "sources_index", rows=3)
            if s.sources_index < len(s.sources):
                item = s.sources[s.sources_index]
                src = st.session.sources.get(item.key)
                box = layout.box()
                col = box.column(align=True)
                col.label(text="%s  -  %s" % (item.kind, human_size(src.size) if src else ""), icon="FILE_ARCHIVE")
                if item.backend:
                    col.label(text="Reader: " + item.backend)
                if item.message:
                    for line in _wrap(item.message, 52):
                        col.label(text=line)
                if src is not None:
                    if src.encrypted:
                        col.label(text="Contains encrypted entries", icon="LOCKED")
                    if src.solid:
                        col.label(text="Solid archive (extracting is sequential)", icon="INFO")
                    for n in src.notes[:3]:
                        for line in _wrap(n, 52):
                            col.label(text=line, icon="INFO")
                if item.status in ("LOCKED",) or (src is not None and src.encrypted):
                    r = box.row(align=True)
                    r.prop(item, "password", text="")
                    r.operator("hjt_fbx_plus.unlock", text="Unlock", icon="UNLOCKED").index = s.sources_index
                if src is not None and src.aidx is not None:
                    r = box.row(align=True)
                    r.prop(s, "tree_filter", text="", icon="VIEWZOOM")
                    r.prop(s, "tree_kind", text="")
                    r.operator("hjt_fbx_plus.tree_expand", text="", icon="DISCLOSURE_TRI_DOWN").collapse = False
                    r.operator("hjt_fbx_plus.tree_expand", text="", icon="DISCLOSURE_TRI_RIGHT").collapse = True
                    box.template_list("HJTFBX_UL_tree", "", s, "tree", s, "tree_index", rows=8)
                    if len(s.tree) >= sync.TREE_ROW_LIMIT:
                        box.label(text="Showing the first %d rows; use the filter" % sync.TREE_ROW_LIMIT, icon="INFO")
                    r = box.row(align=True)
                    r.operator("hjt_fbx_plus.extract", text="Extract Selected...", icon="EXPORT").scope = "ROW"
                    r.operator("hjt_fbx_plus.extract", text="Extract All...").scope = "ALL"
        box = layout.box()
        used, n = cache_usage(st)
        r = box.row()
        r.label(text="Cache: %s in %d files" % (human_size(used), n), icon="FILE_CACHE")
        r.operator("hjt_fbx_plus.cache_clear", text="", icon="TRASH")
        r.operator("hjt_fbx_plus.open_folder", text="", icon="FILE_FOLDER").which = "CACHE"
        from .operators.session_ops import backend_info
        info, _d = backend_info(st)
        col = box.column(align=True)
        col.scale_y = 0.85
        col.label(text="ZIP: built-in", icon="CHECKMARK")
        col.label(text="7Z: built-in" + (" + external" if any("7z" in t["formats"] for t in info["tools"].values()) else ""), icon="CHECKMARK")
        rar_ok = "not available" not in info["rar"]
        col.label(text="RAR: " + info["rar"], icon="CHECKMARK" if rar_ok else "ERROR")
        box.operator("hjt_fbx_plus.tools_detect", icon="VIEWZOOM")


_USAGE = {"t": 0.0, "v": (0, 0)}


def cache_usage(st):
    #walking the cache folder on every redraw would be dumb, refresh every 2s at most
    import time
    now = time.monotonic()
    if now - _USAGE["t"] > 2.0:
        _USAGE["t"] = now
        try:
            _USAGE["v"] = st.cache.usage()
        except OSError:
            _USAGE["v"] = (0, 0)
    return _USAGE["v"]


class HJTFBX_PT_diagnostics(_Base, bpy.types.Panel):
    bl_label = "Import Diagnostics"
    bl_order = 5
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="INFO")

    def draw(self, context):
        layout = self.layout
        st = state.get()
        s = context.window_manager.hjt_fbx_plus
        rep = st.last_report
        if rep:
            box = layout.box()
            t = rep.get("totals", {})
            box.label(text=rep.get("summary", ""), icon="CHECKMARK" if not t.get("failed") else "ERROR")
            g = box.grid_flow(columns=3, even_columns=True, align=True)
            g.label(text="OK: %d" % t.get("ok", 0))
            g.label(text="Warnings: %d" % t.get("warnings", 0))
            g.label(text="Errors: %d" % (t.get("errors", 0) + t.get("failed", 0)))
            r = layout.row(align=True)
            r.prop(s, "msg_filter", expand=True)
            layout.template_list("HJTFBX_UL_messages", "", s, "messages", s, "messages_index", rows=5)
            if s.messages and s.messages_index < len(s.messages):
                msg = s.messages[s.messages_index]
                for line in _wrap(msg.text, 52):
                    layout.label(text=line)
            r = layout.row(align=True)
            r.operator("hjt_fbx_plus.report_open", icon="TEXT")
            r.operator("hjt_fbx_plus.report_copy", text="", icon="COPYDOWN")
        else:
            layout.label(text="Import something to see its report here", icon="INFO")
        layout.operator("hjt_fbx_plus.dump_fbx", icon="OUTLINER")
        box = layout.box()
        r = box.row()
        r.label(text="History", icon="RECOVER_LAST")
        r.operator("hjt_fbx_plus.history_refresh", text="", icon="FILE_REFRESH")
        r.operator("hjt_fbx_plus.history_clear", text="", icon="TRASH")
        box.template_list("HJTFBX_UL_history", "", s, "history", s, "history_index", rows=3)
        if s.history and s.history_index < len(s.history):
            box.operator("hjt_fbx_plus.report_open", text="Open Selected Report", icon="TEXT").rid = s.history[s.history_index].rid
        layout.operator("hjt_fbx_plus.open_folder", text="Open Log Folder", icon="FILE_FOLDER").which = "LOGS"


CLASSES = (HJTFBX_PT_import, HJTFBX_PT_profiles, HJTFBX_PT_profile_editor, HJTFBX_PT_materials,
           HJTFBX_PT_advanced, HJTFBX_PT_archives, HJTFBX_PT_diagnostics)
