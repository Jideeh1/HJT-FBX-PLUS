# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#property groups
# scene settings are generated from core/options.py (one table rules them all)
# the session mirror lives on the window manager = never saved in the .blend (passwords!!)

from __future__ import annotations

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)

from ..core.options import OPTIONS
from ..textures.slots import SLOTS

_profile_items_ref = []


def profile_items(self, context):
    #dynamic enum, keep a ref to the list or blender reads freed strings n shows garbage (classic)
    from .. import state
    items = [("AUTO", "Auto Detect", "Detect the game from material, texture and bone names", "VIEWZOOM", 0)]
    st = state.get()
    if st is not None:
        for i, p in enumerate(st.registry.ordered(), start=1):
            icon = "CHECKMARK" if p.builtin else "USER"
            items.append((p.id, p.name, (p.description or p.name)[:240], icon, i))
    _profile_items_ref[:] = items
    return items


def _prop_for(name, spec):
    t = spec["type"]
    kw = {"name": spec.get("label", name), "description": spec.get("description", "")}
    if t == "bool":
        return BoolProperty(default=spec["default"], **kw)
    if t == "int":
        return IntProperty(default=spec["default"], min=spec.get("min", -2 ** 31), max=spec.get("max", 2 ** 31 - 1), **kw)
    if t == "float":
        return FloatProperty(default=spec["default"], min=spec.get("min", -1e9), max=spec.get("max", 1e9),
                             soft_min=spec.get("min", -1e9), precision=3, **kw)
    if t == "enum":
        return EnumProperty(items=spec["items"], default=spec["default"], **kw)
    return StringProperty(default=spec["default"], subtype=spec.get("subtype", "NONE"), **kw)


def _settings_annotations():
    ann = {name: _prop_for(name, spec) for name, spec in OPTIONS.items()}
    ann["profile"] = EnumProperty(name="Game Profile", items=profile_items,
                                  description="Game profile used for texture discovery and material reconstruction")
    ann["ui_show_armature"] = BoolProperty(name="Armature", default=False)
    ann["ui_show_orientation"] = BoolProperty(name="Orientation", default=False)
    ann["ui_show_animation"] = BoolProperty(name="Animation", default=False)
    return ann


HJTFBX_PG_settings = type("HJTFBX_PG_settings", (bpy.types.PropertyGroup,), {
    "__annotations__": _settings_annotations(),
    "__doc__": "HJT FBX+ import settings",
})


def snapshot(settings):
    #plain dict for worker threads (no bpy objects past this point)
    d = {name: getattr(settings, name) for name in OPTIONS}
    d["profile"] = settings.profile
    return d


#session mirror

def _model_selected_update(self, context):
    from .. import state
    st = state.get()
    if st is None:
        return
    m = st.session.models.get(self.key)
    if m is not None and m.selected != self.selected:
        m.selected = self.selected


STATUS_ITEMS = [
    ("PENDING", "Pending", "", "TIME", 0), ("ANALYZING", "Analyzing", "", "SORTTIME", 1),
    ("READY", "Ready", "", "CHECKMARK", 2), ("WARN", "Warnings", "", "ERROR", 3),
    ("ERROR", "Error", "", "CANCEL", 4), ("IMPORTED", "Imported", "", "IMPORT", 5),
]


class HJTFBX_PG_model(bpy.types.PropertyGroup):
    key: StringProperty()
    label: StringProperty()
    source: StringProperty()
    member: StringProperty()
    status: EnumProperty(items=STATUS_ITEMS, default="PENDING")
    selected: BoolProperty(name="Import", default=True, update=_model_selected_update,
                           description="Include this model in the next import")
    message: StringProperty()
    profile: StringProperty()
    size: IntProperty()


class HJTFBX_PG_source(bpy.types.PropertyGroup):
    key: StringProperty()
    label: StringProperty()
    kind: StringProperty()
    status: StringProperty()
    message: StringProperty()
    backend: StringProperty()
    password: StringProperty(name="Password", subtype="PASSWORD", options={"SKIP_SAVE"},
                             description="Archive password (kept in memory only, never saved)")


class HJTFBX_PG_tree(bpy.types.PropertyGroup):
    path: StringProperty()
    depth: IntProperty()
    is_dir: BoolProperty()
    expanded: BoolProperty()
    kind: StringProperty()
    size: IntProperty()
    info: StringProperty()


class HJTFBX_PG_message(bpy.types.PropertyGroup):
    level: StringProperty()
    text: StringProperty()
    model: StringProperty()


class HJTFBX_PG_history(bpy.types.PropertyGroup):
    rid: StringProperty()
    summary: StringProperty()
    when: StringProperty()
    names: StringProperty()


class HJTFBX_PG_profile_row(bpy.types.PropertyGroup):
    pid: StringProperty()
    builtin: BoolProperty()


#profile editor (mirror of the editable bits of a profile json)

SLOT_ITEMS = [(k, v["label"], "") for k, v in SLOTS.items()]


class HJTFBX_PG_rule(bpy.types.PropertyGroup):
    slot: EnumProperty(name="Slot", items=SLOT_ITEMS)
    pattern: StringProperty(name="Pattern", description="Regular expression (matched case-insensitively against the lowercase file name)")
    key: StringProperty(name="Key", description="Unity material property, e.g. _MainTex")


class HJTFBX_PG_matrule(bpy.types.PropertyGroup):
    match: StringProperty(name="Match", description="Regular expression matched against material names")
    category: StringProperty(name="Category")
    #empty enum identifiers r separators in blender, so "use the default" needs a real id
    template: EnumProperty(name="Template", items=[("DEFAULT", "Default", "Use the profile default"), ("PBR", "PBR", ""),
                                                   ("TOON", "Toon", ""), ("FLAT", "Unlit", ""), ("KEEP", "Keep", "")], default="DEFAULT")
    alpha_mode: EnumProperty(name="Alpha", items=[("DEFAULT", "Default", "Use the profile default"), ("AUTO", "Auto", ""),
                                                  ("OPAQUE", "Opaque", ""), ("CLIP", "Clip", ""), ("HASHED", "Hashed", ""),
                                                  ("BLEND", "Blend", "")], default="DEFAULT")


class HJTFBX_PG_category(bpy.types.PropertyGroup):
    name: StringProperty(name="Name")
    pattern: StringProperty(name="Pattern", description="Regular expression matched against object names")
    action: EnumProperty(name="Action", items=[("HIDE", "Hide", ""), ("WIRE", "Wireframe", ""),
                                               ("COLLECTION", "Collection", ""), ("KEEP", "Tag Only", "")], default="HIDE")


class HJTFBX_PG_profile_edit(bpy.types.PropertyGroup):
    loaded_id: StringProperty()
    name: StringProperty(name="Name")
    description: StringProperty(name="Description")
    detect_min: FloatProperty(name="Detection Score", min=0.0, max=100.0, description="Minimum score needed to auto-select this profile")
    template: EnumProperty(name="Material Template", items=OPTIONS["material_mode"]["items"][1:], default="PBR")
    alpha_mode: EnumProperty(name="Alpha", items=[(i, i.title(), "") for i in ("AUTO", "OPAQUE", "CLIP", "HASHED", "BLEND")], default="AUTO")
    base_alpha_usage: EnumProperty(name="Base Color Alpha", items=[
        ("AUTO", "Auto", "Use base color alpha for transparency only when the material is transparent"),
        ("NONE", "Ignore", "Base color alpha holds data, never transparency"),
        ("TRANSPARENCY", "Transparency", "Base color alpha is transparency"),
        ("EMISSION_MASK", "Emission Mask", "Base color alpha marks glowing areas")], default="AUTO")
    backface_culling: EnumProperty(name="Backface Culling", items=[("AUTO", "Auto (game data)", ""), ("ON", "On", ""), ("OFF", "Off", "")], default="AUTO")
    vertex_color: EnumProperty(name="Vertex Color", items=[("NONE", "Ignore", ""), ("MULTIPLY", "Multiply", ""), ("AUTO", "When No Texture", "")], default="NONE")
    normal_convention: EnumProperty(name="Normal Maps", items=[("OPENGL", "OpenGL (Y+)", ""), ("DIRECTX", "DirectX (Y-)", "")], default="OPENGL")
    normal_format: EnumProperty(name="Normal Format", items=[("AUTO", "Auto", ""), ("RGB", "RGB", ""), ("RG", "RG (BC5)", ""), ("AG", "AG (DXT5nm)", "")], default="AUTO")
    shadow_tint: FloatVectorProperty(name="Shadow Tint", subtype="COLOR", size=3, min=0.0, max=1.0, default=(0.78, 0.74, 0.84))
    shadow_threshold: FloatProperty(name="Shadow Threshold", min=0.0, max=1.0, default=0.5)
    align_bone_chains: BoolProperty(name="Align Bone Chains", description="Point bones at the next bone of their chain (mesh and animation unchanged)")
    fit_bone_lengths: BoolProperty(name="Fit Bone Lengths")
    verify_skinning: BoolProperty(name="Verify Skinning", default=True)
    use_material_json: BoolProperty(name="Material JSON", default=True)
    use_fbx_references: BoolProperty(name="FBX Texture Links", default=True)
    name_matching: BoolProperty(name="Match by Name", default=True)
    lod_action: EnumProperty(name="LODs", items=[("HIDE_LOWER", "Hide Lower", ""), ("COLLECTIONS", "Collections", ""), ("KEEP", "Keep Visible", "")], default="HIDE_LOWER")
    collision_action: EnumProperty(name="Collision", items=[("WIRE_HIDE", "Wireframe", ""), ("HIDE", "Hide", ""), ("KEEP", "Keep", "")], default="WIRE_HIDE")
    slot_rules: CollectionProperty(type=HJTFBX_PG_rule)
    slot_rules_index: IntProperty()
    unity_slots: CollectionProperty(type=HJTFBX_PG_rule)
    unity_slots_index: IntProperty()
    material_rules: CollectionProperty(type=HJTFBX_PG_matrule)
    material_rules_index: IntProperty()
    categories: CollectionProperty(type=HJTFBX_PG_category)
    categories_index: IntProperty()
    dirty: BoolProperty()


class HJTFBX_PG_session(bpy.types.PropertyGroup):
    models: CollectionProperty(type=HJTFBX_PG_model)
    models_index: IntProperty(name="Model")
    sources: CollectionProperty(type=HJTFBX_PG_source)
    sources_index: IntProperty(name="Source")
    tree: CollectionProperty(type=HJTFBX_PG_tree)
    tree_index: IntProperty()
    tree_filter: StringProperty(name="Filter", description="Show files whose path contains this text", options={"TEXTEDIT_UPDATE"})
    tree_kind: EnumProperty(name="Show", items=[("ALL", "All", ""), ("FBX", "FBX", ""), ("IMAGE", "Images", ""), ("DATA", "Data", "")], default="ALL")
    messages: CollectionProperty(type=HJTFBX_PG_message)
    messages_index: IntProperty()
    msg_filter: EnumProperty(name="Level", items=[("ALL", "All", ""), ("WARNING", "Warnings", ""), ("ERROR", "Errors", "")], default="ALL")
    history: CollectionProperty(type=HJTFBX_PG_history)
    history_index: IntProperty()
    profiles: CollectionProperty(type=HJTFBX_PG_profile_row)
    profiles_index: IntProperty()
    profile_edit: PointerProperty(type=HJTFBX_PG_profile_edit)
    progress: FloatProperty(min=0.0, max=1.0)
    status_text: StringProperty()
    running: BoolProperty()
    last_summary: StringProperty()
    preview_selected_only: BoolProperty(name="Selected Only", default=True)


CLASSES = (
    HJTFBX_PG_settings, HJTFBX_PG_model, HJTFBX_PG_source, HJTFBX_PG_tree, HJTFBX_PG_message, HJTFBX_PG_history,
    HJTFBX_PG_profile_row, HJTFBX_PG_rule, HJTFBX_PG_matrule, HJTFBX_PG_category, HJTFBX_PG_profile_edit,
    HJTFBX_PG_session,
)


def register_props():
    bpy.types.Scene.hjt_fbx_plus = PointerProperty(type=HJTFBX_PG_settings)
    bpy.types.WindowManager.hjt_fbx_plus = PointerProperty(type=HJTFBX_PG_session)


def unregister_props():
    for owner in (bpy.types.Scene, bpy.types.WindowManager):
        if hasattr(owner, "hjt_fbx_plus"):
            del owner.hjt_fbx_plus
