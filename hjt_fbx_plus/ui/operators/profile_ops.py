# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#game profile operators: new / duplicate / delete / export / import / reload + the editor
# the editor covers the common stuff, "Edit JSON" opens the whole profile in the text editor

from __future__ import annotations

import copy
import json
import os

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from ... import state
from ...profiles.registry import ProfileError
from ...profiles.schema import validate
from .. import sync

TEXT_PREFIX = "HJT Profile: "


def _active_pid(context):
    s = context.window_manager.hjt_fbx_plus
    if s.profiles and s.profiles_index < len(s.profiles):
        return s.profiles[s.profiles_index].pid
    return "generic"


def _refresh(context):
    st = state.get()
    s = context.window_manager.hjt_fbx_plus
    s.profiles.clear()
    sync.sync_profiles(s, st)


def _select(context, pid):
    s = context.window_manager.hjt_fbx_plus
    for i, r in enumerate(s.profiles):
        if r.pid == pid:
            s.profiles_index = i
            break


class HJTFBX_OT_profile_new(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.profile_new"
    bl_label = "New Profile"
    bl_description = "Create a new empty game profile (starts from the Generic defaults)"

    name: StringProperty(name="Name", default="My Game")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        st = state.get()
        try:
            pid = st.registry.create(self.name.strip() or "My Game")
        except ProfileError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _refresh(context)
        _select(context, pid)
        bpy.ops.hjt_fbx_plus.profile_edit_load()
        return {"FINISHED"}


class HJTFBX_OT_profile_duplicate(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.profile_duplicate"
    bl_label = "Duplicate Profile"
    bl_description = "Copy the selected profile so you can customize it"

    def execute(self, context):
        st = state.get()
        try:
            pid = st.registry.duplicate(_active_pid(context))
        except ProfileError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _refresh(context)
        _select(context, pid)
        bpy.ops.hjt_fbx_plus.profile_edit_load()
        return {"FINISHED"}


class HJTFBX_OT_profile_delete(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.profile_delete"
    bl_label = "Delete Profile"
    bl_description = "Delete the selected user profile (a customized built-in profile goes back to its original)"

    @classmethod
    def poll(cls, context):
        st = state.get()
        if st is None:
            return False
        p = st.registry.profiles.get(_active_pid(context))
        return p is not None and not p.builtin

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        st = state.get()
        try:
            st.registry.delete(_active_pid(context))
        except ProfileError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _refresh(context)
        return {"FINISHED"}


class HJTFBX_OT_profile_export(bpy.types.Operator, ExportHelper):
    bl_idname = "hjt_fbx_plus.profile_export"
    bl_label = "Export Profile"
    bl_description = "Save the selected profile as a .json file to share it"

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def invoke(self, context, event):
        self.filepath = _active_pid(context) + ".json"
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        st = state.get()
        st.registry.export(_active_pid(context), self.filepath)
        self.report({"INFO"}, "Exported to %s" % self.filepath)
        return {"FINISHED"}


class HJTFBX_OT_profile_import(bpy.types.Operator, ImportHelper):
    bl_idname = "hjt_fbx_plus.profile_import"
    bl_label = "Import Profile"
    bl_description = "Add a profile from a .json file"

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        st = state.get()
        try:
            pid = st.registry.import_file(self.filepath)
        except ProfileError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _refresh(context)
        _select(context, pid)
        self.report({"INFO"}, "Imported profile '%s'" % st.registry.get(pid).name)
        return {"FINISHED"}


class HJTFBX_OT_profile_reload(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.profile_reload"
    bl_label = "Reload Profiles"
    bl_description = "Reload all profiles from disk"

    def execute(self, context):
        st = state.get()
        st.registry.reload()
        _refresh(context)
        if st.registry.errors:
            self.report({"WARNING"}, "; ".join(st.registry.errors[:3]))
        return {"FINISHED"}


class HJTFBX_OT_profile_use(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.profile_use"
    bl_label = "Use for Imports"
    bl_description = "Use the selected profile for the next imports (instead of auto detection)"

    def execute(self, context):
        context.scene.hjt_fbx_plus.profile = _active_pid(context)
        return {"FINISHED"}


#editor

def load_editor(pe, profile):
    d = profile.data
    md = profile.material_default
    pe.loaded_id = profile.id
    pe.name = d.get("name", "")
    pe.description = d.get("description", "")
    pe.detect_min = float((d.get("detect") or {}).get("min_score", 0.0))
    for key in ("template", "alpha_mode", "base_alpha_usage", "backface_culling", "vertex_color", "normal_convention", "normal_format"):
        try:
            setattr(pe, key, md.get(key) or getattr(pe, key))
        except TypeError:
            pass
    tint = md.get("shadow_tint") or (0.78, 0.74, 0.84)
    pe.shadow_tint = tuple(float(x) for x in tint[:3])
    pe.shadow_threshold = float(md.get("shadow_threshold", 0.5))
    corr = d.get("corrections") or {}
    pe.align_bone_chains = bool(corr.get("align_bone_chains", False))
    pe.fit_bone_lengths = bool(corr.get("fit_bone_lengths", False))
    pe.verify_skinning = bool(corr.get("verify_skinning", True))
    tex = d.get("textures") or {}
    pe.use_material_json = bool(tex.get("use_material_json", True))
    pe.use_fbx_references = bool(tex.get("use_fbx_references", True))
    pe.name_matching = bool(tex.get("name_matching", True))
    cats = d.get("categories") or {}
    pe.lod_action = (cats.get("lod") or {}).get("action", "HIDE_LOWER")
    pe.collision_action = (cats.get("collision") or {}).get("action", "WIRE_HIDE")
    raw = profile.raw
    rtex = raw.get("textures") or {}
    pe.slot_rules.clear()
    for r in rtex.get("slots", []):
        it = pe.slot_rules.add()
        try:
            it.slot = r.get("slot", "aux")
        except TypeError:
            it.slot = "aux"
        it.pattern = r.get("pattern", "")
    pe.unity_slots.clear()
    for k, v in (rtex.get("unity_slots") or {}).items():
        it = pe.unity_slots.add()
        it.key = k
        try:
            it.slot = v
        except TypeError:
            it.slot = "aux"
    pe.material_rules.clear()
    for r in (raw.get("materials") or {}).get("rules", []):
        it = pe.material_rules.add()
        it.match = r.get("match", "")
        it.category = r.get("category", "")
        st_ = r.get("settings") or {}
        try:
            it.template = st_.get("template") or "DEFAULT"
        except TypeError:
            it.template = "DEFAULT"
        try:
            it.alpha_mode = st_.get("alpha_mode") or "DEFAULT"
        except TypeError:
            it.alpha_mode = "DEFAULT"
    pe.categories.clear()
    for c in (raw.get("categories") or {}).get("custom", []):
        it = pe.categories.add()
        it.name = c.get("name", "")
        it.pattern = c.get("pattern", "")
        try:
            it.action = c.get("action", "HIDE")
        except TypeError:
            it.action = "HIDE"
    pe.dirty = False


def editor_to_json(pe, profile):
    raw = copy.deepcopy(profile.raw)
    raw["schema"] = 1
    raw["id"] = profile.id
    raw["name"] = pe.name.strip() or profile.name
    raw["description"] = pe.description
    raw.setdefault("detect", {})["min_score"] = round(pe.detect_min, 3)
    mats = raw.setdefault("materials", {})
    md = mats.setdefault("default", {})
    for key in ("template", "alpha_mode", "base_alpha_usage", "backface_culling", "vertex_color", "normal_convention", "normal_format"):
        md[key] = getattr(pe, key)
    md["shadow_tint"] = [round(float(x), 4) for x in pe.shadow_tint]
    md["shadow_threshold"] = round(pe.shadow_threshold, 4)
    rules = []
    old_rules = mats.get("rules", [])
    for i, it in enumerate(pe.material_rules):
        base = copy.deepcopy(old_rules[i]) if i < len(old_rules) and old_rules[i].get("match") == it.match else {}
        base["match"] = it.match
        if it.category:
            base["category"] = it.category
        else:
            base.pop("category", None)
        settings = base.setdefault("settings", {})
        for key in ("template", "alpha_mode"):
            v = getattr(it, key)
            if v and v != "DEFAULT":
                settings[key] = v
            else:
                settings.pop(key, None)
        if not settings:
            base.pop("settings", None)
        rules.append(base)
    mats["rules"] = rules
    corr = raw.setdefault("corrections", {})
    corr["align_bone_chains"] = pe.align_bone_chains
    corr["fit_bone_lengths"] = pe.fit_bone_lengths
    corr["verify_skinning"] = pe.verify_skinning
    tex = raw.setdefault("textures", {})
    tex["use_material_json"] = pe.use_material_json
    tex["use_fbx_references"] = pe.use_fbx_references
    tex["name_matching"] = pe.name_matching
    tex["slots"] = [{"slot": r.slot, "pattern": r.pattern} for r in pe.slot_rules if r.pattern]
    tex["unity_slots"] = {r.key: r.slot for r in pe.unity_slots if r.key}
    cats = raw.setdefault("categories", {})
    cats.setdefault("lod", {})["action"] = pe.lod_action
    cats.setdefault("collision", {})["action"] = pe.collision_action
    cats["custom"] = [{"name": c.name, "pattern": c.pattern, "action": c.action} for c in pe.categories if c.pattern]
    return raw


class HJTFBX_OT_profile_edit_load(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.profile_edit_load"
    bl_label = "Edit"
    bl_description = "Load the selected profile into the editor"

    def execute(self, context):
        st = state.get()
        p = st.registry.get(_active_pid(context))
        load_editor(context.window_manager.hjt_fbx_plus.profile_edit, p)
        return {"FINISHED"}


class HJTFBX_OT_profile_edit_save(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.profile_edit_save"
    bl_label = "Save Profile"
    bl_description = "Save the editor values. Built-in profiles are saved as your customized copy (Delete restores the original)"

    def execute(self, context):
        st = state.get()
        pe = context.window_manager.hjt_fbx_plus.profile_edit
        p = st.registry.profiles.get(pe.loaded_id)
        if p is None:
            self.report({"ERROR"}, "Load a profile into the editor first")
            return {"CANCELLED"}
        data = editor_to_json(pe, p)
        try:
            st.registry.save(data)
        except ProfileError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        pe.dirty = False
        _refresh(context)
        _select(context, data["id"])
        self.report({"INFO"}, "Saved profile '%s'" % data["name"])
        return {"FINISHED"}


class HJTFBX_OT_profile_rule(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.profile_rule"
    bl_label = "Edit Rule List"
    bl_description = "Add or remove an entry"

    target: EnumProperty(items=[("slot_rules", "Slot Rules", ""), ("unity_slots", "Unity Slots", ""),
                                ("material_rules", "Material Rules", ""), ("categories", "Categories", "")])
    action: EnumProperty(items=[("ADD", "Add", ""), ("REMOVE", "Remove", "")])

    def execute(self, context):
        pe = context.window_manager.hjt_fbx_plus.profile_edit
        coll = getattr(pe, self.target)
        idx_name = self.target + "_index"
        if self.action == "ADD":
            coll.add()
            setattr(pe, idx_name, len(coll) - 1)
        else:
            i = getattr(pe, idx_name)
            if 0 <= i < len(coll):
                coll.remove(i)
                setattr(pe, idx_name, max(0, i - 1))
        pe.dirty = True
        return {"FINISHED"}


def _show_text(context, text):
    for area in context.screen.areas:
        if area.type == "TEXT_EDITOR":
            area.spaces.active.text = text
            return True
    try:
        bpy.ops.wm.window_new()
        win = context.window_manager.windows[-1]
        area = win.screen.areas[0]
        area.type = "TEXT_EDITOR"
        area.spaces.active.text = text
        return True
    except (RuntimeError, IndexError, AttributeError):
        return False


class HJTFBX_OT_profile_json(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.profile_json"
    bl_label = "Edit JSON"
    bl_description = "Open the full profile JSON in the Text Editor (use Apply JSON to save it)"

    def execute(self, context):
        st = state.get()
        p = st.registry.get(_active_pid(context))
        name = TEXT_PREFIX + p.id
        txt = bpy.data.texts.get(name) or bpy.data.texts.new(name)
        txt.from_string(json.dumps(p.to_json(), indent=2, ensure_ascii=False) + "\n")
        if not _show_text(context, txt):
            self.report({"INFO"}, "Open the text '%s' in a Text Editor" % name)
        return {"FINISHED"}


class HJTFBX_OT_profile_json_apply(bpy.types.Operator):
    bl_idname = "hjt_fbx_plus.profile_json_apply"
    bl_label = "Apply JSON"
    bl_description = "Validate and save the profile JSON from the Text Editor"

    def execute(self, context):
        st = state.get()
        p = st.registry.get(_active_pid(context))
        txt = bpy.data.texts.get(TEXT_PREFIX + p.id)
        if txt is None:
            self.report({"ERROR"}, "Press Edit JSON first")
            return {"CANCELLED"}
        try:
            data = json.loads(txt.as_string())
        except ValueError as exc:
            self.report({"ERROR"}, "Invalid JSON: %s" % exc)
            return {"CANCELLED"}
        problems = validate(data)
        if problems:
            self.report({"ERROR"}, "; ".join(problems[:4]))
            return {"CANCELLED"}
        try:
            st.registry.save(data)
        except ProfileError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _refresh(context)
        _select(context, data["id"])
        bpy.ops.hjt_fbx_plus.profile_edit_load()
        self.report({"INFO"}, "Saved profile '%s'" % data.get("name"))
        return {"FINISHED"}


CLASSES = (HJTFBX_OT_profile_new, HJTFBX_OT_profile_duplicate, HJTFBX_OT_profile_delete, HJTFBX_OT_profile_export,
           HJTFBX_OT_profile_import, HJTFBX_OT_profile_reload, HJTFBX_OT_profile_use, HJTFBX_OT_profile_edit_load,
           HJTFBX_OT_profile_edit_save, HJTFBX_OT_profile_rule, HJTFBX_OT_profile_json, HJTFBX_OT_profile_json_apply)
