# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#3d view sidebar, tab "HJT FBX+"
#  HJT FBX+    big import button (fbx or 1 model from a zip/7z/rar), live progress, update banner, options
#  Selected    changes w whatever u clicked: armature -> chain health + fix, mesh -> material tools
#  Game Profiles / Diagnostics
#
#5.2 goodies used when they exist: layout.panel() fold-outs w header toggles, progress RING, LINE
# separators. older blenders get plain boxes. draw code reads cached state only, NEVER scans or
#parses anything, draw runs like 60 times a sec when u wiggle the mouse

from __future__ import annotations

import bpy

from .. import state
from ..blender import chains, compat, updater
from ..blender.materials import META_PROP
from . import prefs as prefs_mod

CATEGORY = "HJT FBX+"
_HAS_SEP_TYPE = False
try:
    _HAS_SEP_TYPE = "type" in bpy.types.UILayout.bl_rna.functions["separator"].parameters
except Exception:  # noqa: BLE001
    pass


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


def _line(layout):
    if _HAS_SEP_TYPE:
        layout.separator(type="LINE")
    else:
        layout.separator()


def _section(layout, idname, title, icon="NONE", closed=True, toggle=None, chip=""):
    #fold-out section. toggle=(data, prop) puts a checkbox in the header n greys the body when off
    # returns (body or None, enabled)
    if compat.HAS_LAYOUT_PANEL:
        header, body = layout.panel("hjt_" + idname, default_closed=closed)
        row = header.row(align=True)
        if toggle is not None:
            row.prop(toggle[0], toggle[1], text="")
            row.label(text=title, icon=icon)
        else:
            row.label(text=title, icon=icon)
        if chip:
            c = header.row()
            c.alignment = "RIGHT"
            c.label(text=chip)
        on = True if toggle is None else bool(getattr(toggle[0], toggle[1]))
        if body is not None:
            body.active = on
        return body, on
    box = layout.box()
    row = box.row(align=True)
    if toggle is not None:
        row.prop(toggle[0], toggle[1], text="")
    row.label(text=title + (("  -  " + chip) if chip else ""), icon=icon)
    on = True if toggle is None else bool(getattr(toggle[0], toggle[1]))
    box.active = on
    return box, on


def _enum_name(data, prop):
    try:
        return data.bl_rna.properties[prop].enum_items[getattr(data, prop)].name
    except (KeyError, AttributeError):
        return ""


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


def _wrap_for(context, text):
    #wrap to whatever the sidebar is right now. wider sidebar = longer lines, its the little things
    width = 260
    region = getattr(context, "region", None)
    if region is not None:
        width = region.width
    scale = max(0.5, context.preferences.system.ui_scale)
    return _wrap(text, max(18, int(width / (7.0 * scale))))


def _active_profile(context):
    st = state.get()
    s = _settings(context)
    if s.profile == "AUTO":
        return None
    return st.registry.profiles.get(s.profile) if st is not None else None


def _profile_wants(context, key):
    p = _active_profile(context)
    if p is None:
        return None  #auto: decided per file
    return bool(p.corrections.get(key, False))


class HJTFBX_PT_main(_Base, bpy.types.Panel):
    bl_label = "HJT FBX+"
    bl_order = 0

    def draw_header(self, context):
        self.layout.label(text="", icon="OUTLINER_OB_ARMATURE")

    def draw_header_preset(self, context):
        st = updater.STATUS
        row = self.layout.row(align=True)
        if st.state in ("AVAILABLE", "READY"):
            row.alert = True
            row.operator("hjt_fbx_plus.update_install", text=st.latest, icon="IMPORT", emboss=False)
        elif st.state == "INSTALLED":
            row.alert = True
            row.label(text="restart", icon="FILE_REFRESH")
        else:
            row.label(text=state.addon_version())

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager.hjt_fbx_plus
        s = _settings(context)
        st = state.get()

        if updater.STATUS.state in ("AVAILABLE", "READY", "DOWNLOADING", "INSTALLED"):
            prefs_mod.draw_update_block(layout, updater)

        if wm.running:
            box = layout.box()
            compat.draw_progress(box, wm.progress, wm.status_text or "Importing...")
            r = box.row()
            r.active = False
            r.label(text="Esc cancels after the current step", icon="CANCEL")
        else:
            col = layout.column(align=True)
            r = col.row(align=True)
            r.scale_y = 1.8
            r.operator("hjt_fbx_plus.import_files", text="Import FBX/UEMODEL", icon="IMPORT")
            if wm.status_text:
                #archive still being read in the background
                compat.draw_progress(col, 0.5, wm.status_text, ring=True)

        col = layout.column(align=True)
        col.prop(s, "profile", text="", icon="PRESET")
        p = _active_profile(context)
        if p is not None and p.description:
            sub = col.column(align=True)
            sub.scale_y = 0.75
            sub.active = False
            for line in _wrap_for(context, p.description)[:2]:
                sub.label(text=line)

        #quick strip, the 2 things u actually change per model
        g = layout.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
        g.prop(s, "align_chains", text="", icon="BONE_DATA")
        g.prop(s, "material_mode", text="", icon="MATERIAL")

        if wm.last_summary and not wm.running:
            _draw_last_result(layout, st, wm)

        _line(layout)
        self._options(context, layout, s)
        _line(layout)
        layout.operator("wm.url_open", text="Get More Models at hjtstudio.com", icon="URL").url = "https://hjtstudio.com"

    def _options(self, context, layout, s):
        body, _ = _section(layout, "mats", "Materials & Textures", "MATERIAL", chip=_enum_name(s, "material_mode"))
        if body:
            c = _split(body)
            c.prop(s, "material_mode")
            if s.material_mode == "TEMPLATE":
                c.prop_search(s, "template_material", bpy.data, "materials")
                if s.template_material and s.template_material not in bpy.data.materials:
                    w = c.row()
                    w.alert = True
                    w.label(text="Material not found in this file", icon="ERROR")
            c.separator()
            c.prop(s, "use_fbx_references")
            c.prop(s, "use_material_json")
            c.prop(s, "name_matching")
            sub = c.column(align=True)
            sub.active = s.name_matching
            sub.prop(s, "search_depth")
            sub.prop(s, "search_parent_levels")
            c.separator()
            c.prop(s, "texture_storage")
            if s.texture_storage == "COPY":
                c.prop(s, "copy_dir")
            c.prop(s, "reuse_images")

        wants = _profile_wants(context, "align_bone_chains")
        will_align = s.align_chains == "ON" or (s.align_chains == "PROFILE" and wants is not False)
        chip = {"ON": "Align", "OFF": "Off"}.get(s.align_chains, "Auto" if wants is None else ("Align" if wants else "Off"))
        body, _ = _section(layout, "arm", "Armature", "ARMATURE_DATA", chip=chip)
        if body:
            c = _split(body)
            c.prop(s, "align_chains")
            if s.align_chains == "PROFILE":
                info = c.row()
                info.active = False
                info.label(text={None: "Follows the detected game profile", True: "This profile aligns chains",
                                 False: "This profile keeps importer bones"}[wants], icon="INFO")
            fit = c.row()
            fit.active = not will_align
            fit.prop(s, "fit_bones")
            if will_align:
                n = c.row()
                n.active = False
                n.label(text="Chain alignment already sizes bones", icon="CHECKMARK")
            c.prop(s, "verify_skinning")
            c.prop(s, "ignore_leaf_bones")
            py = s.engine != "CPP"
            sub = c.column()
            sub.active = py
            sub.prop(s, "force_connect_children")
            sub.prop(s, "automatic_bone_orientation")
            ax = sub.column(align=True)
            ax.active = py and not s.automatic_bone_orientation
            ax.prop(s, "primary_bone_axis")
            ax.prop(s, "secondary_bone_axis")
            sub.prop(s, "use_prepost_rot")
            if not py:
                w = c.row()
                w.active = False
                w.label(text="C++ engine ignores these", icon="INFO")

        body, on = _section(layout, "anim", "Animation", "ACTION", toggle=(s, "use_anim"))
        if body:
            c = _split(body)
            c.prop(s, "anim_offset")

        body, _ = _section(layout, "geo", "Geometry & Data", "MESH_DATA")
        if body:
            c = _split(body)
            c.prop(s, "global_scale")
            for k in ("use_custom_normals", "colors_type", "keep_tangents", "use_custom_props", "use_subsurf"):
                c.prop(s, k)
            c.prop(s, "split_inbetweens")
            r = c.row()
            r.active = s.split_inbetweens
            r.prop(s, "inbetween_drivers")

        body, on = _section(layout, "orient", "Orientation", "ORIENTATION_GLOBAL", toggle=(s, "use_manual_orientation"))
        if body:
            c = _split(body)
            c.active = on and s.engine != "CPP"
            c.prop(s, "axis_forward")
            c.prop(s, "axis_up")
            c.prop(s, "bake_space_transform")
            if s.axis_forward[-1] == s.axis_up[-1]:
                w = body.row()
                w.alert = True
                w.label(text="Forward and Up can't use the same axis", icon="ERROR")

        body, _ = _section(layout, "org", "Scene Organization", "OUTLINER_COLLECTION")
        if body:
            c = _split(body)
            for k in ("organize", "group_by_category", "handle_lods", "handle_collision", "tag_metadata"):
                c.prop(s, k)

        body, _ = _section(layout, "engine", "Engine & Presets", "PREFERENCES", chip=_enum_name(s, "engine"))
        if body:
            r = body.row(align=True)
            r.menu("HJTFBX_MT_presets", text="Presets", icon="PRESET")
            r.operator("hjt_fbx_plus.preset_add", text="", icon="ADD")
            r.operator("hjt_fbx_plus.preset_add", text="", icon="REMOVE").remove_active = True
            c = _split(body)
            c.prop(s, "engine")
            c.prop(s, "apply_profile_import")
            if s.engine == "CPP" and bpy.app.version < (4, 5, 0):
                w = c.row()
                w.alert = True
                w.label(text="C++ importer needs Blender 4.5+", icon="ERROR")


def _draw_last_result(layout, st, wm):
    rep = st.last_report if st is not None else None
    t = (rep or {}).get("totals", {})
    failed = t.get("failed", 0)
    warn = t.get("warnings", 0)
    box = layout.box()
    r = box.row(align=True)
    r.alert = bool(failed)
    r.label(text=wm.last_summary, icon="ERROR" if failed else ("INFO" if warn else "CHECKMARK"))
    r.operator("hjt_fbx_plus.report_open", text="", icon="TEXT")
    models = (rep or {}).get("models", [])
    if models:
        m = models[-1]
        sub = box.column(align=True)
        sub.scale_y = 0.8
        sub.active = False
        stats = m.get("stats", {})
        bits = []
        if m.get("profile"):
            bits.append(m["profile"].replace("_", " ").title())
        if stats.get("bones"):
            bits.append("%d bones" % stats["bones"])
        if stats.get("materials"):
            bits.append("%d materials" % stats["materials"])
        if bits:
            sub.label(text="  ·  ".join(bits))
        if m.get("error"):
            for line in _wrap(m["error"], 44)[:3]:
                e = box.row()
                e.alert = True
                e.label(text=line)


class HJTFBX_PT_selected(_Base, bpy.types.Panel):
    bl_label = "Selected"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return _Base.poll(context) and context.active_object is not None and context.active_object.type in ("ARMATURE", "MESH")

    def draw_header(self, context):
        ob = context.active_object
        self.layout.label(text="", icon="OUTLINER_OB_ARMATURE" if ob.type == "ARMATURE" else "OUTLINER_OB_MESH")

    def draw_header_preset(self, context):
        self.layout.label(text=context.active_object.name[:22])

    def draw(self, context):
        layout = self.layout
        ob = context.active_object
        arm = ob if ob.type == "ARMATURE" else ob.find_armature()
        if arm is not None:
            self._armature(context, layout, arm)
        if ob.type == "MESH":
            if arm is not None:
                _line(layout)
            self._materials(context, layout, ob)

    def _armature(self, context, layout, arm):
        good, total = chains.chain_health(arm)
        frac = (good / float(total)) if total else 1.0
        col = layout.column(align=True)
        col.label(text="%s  -  %d bones" % (arm.name, len(arm.data.bones)), icon="ARMATURE_DATA")
        if total:
            compat.draw_progress(col, frac, "%d / %d bones on their chain" % (good, total))
        r = layout.row()
        r.scale_y = 1.3
        r.alert = frac < 0.6
        r.operator("hjt_fbx_plus.align_chains", text="Align Bone Chains" if frac < 0.95 else "Re-align Chains", icon="BONE_DATA")
        if arm.animation_data and (arm.animation_data.action or arm.animation_data.nla_tracks):
            n = layout.row()
            n.active = False
            n.label(text="Animation is kept, keys get re-expressed", icon="ACTION")

    def _materials(self, context, layout, ob):
        mat = ob.active_material
        col = layout.column(align=True)
        if mat is None:
            col.label(text="No material", icon="MATERIAL")
        else:
            meta = mat.get(META_PROP)
            col.label(text=mat.name, icon="MATERIAL")
            if meta:
                sub = col.column(align=True)
                sub.scale_y = 0.8
                sub.active = False
                sub.label(text="%s  ·  %d faithful, %d inferred" % (meta.get("shading", ""), meta.get("textures_faithful", 0),
                                                                     meta.get("textures_inferred", 0)))
                if meta.get("game_shader"):
                    sub.label(text="Game shader: %s" % meta["game_shader"])
            else:
                sub = col.row()
                sub.active = False
                sub.label(text="Not created by HJT FBX+")
        c = layout.column(align=True)
        c.operator("hjt_fbx_plus.rebuild_materials", icon="FILE_REFRESH")
        c.operator("hjt_fbx_plus.find_missing", icon="VIEWZOOM")
        c.operator("hjt_fbx_plus.reassign_texture", text="Assign Texture to Slot...", icon="IMAGE_DATA")


class HJTFBX_PT_profiles(_Base, bpy.types.Panel):
    bl_label = "Game Profiles"
    bl_order = 2
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
        col.prop(pe, "align_bone_chains")
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


class HJTFBX_PT_diagnostics(_Base, bpy.types.Panel):
    bl_label = "Import Diagnostics"
    bl_order = 3
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


CLASSES = (HJTFBX_PT_main, HJTFBX_PT_selected, HJTFBX_PT_profiles, HJTFBX_PT_profile_editor, HJTFBX_PT_diagnostics)
