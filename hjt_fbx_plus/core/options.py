# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#every import option in ONE table. the scene property group is generated from this, the worker
# threads get a plain dict snapshot of it, presets save it. one source of truth, no drift

from __future__ import annotations

AXES = [("X", "X", ""), ("Y", "Y", ""), ("Z", "Z", ""), ("-X", "-X", ""), ("-Y", "-Y", ""), ("-Z", "-Z", "")]

OPTIONS = {
    #core
    "engine": {"type": "enum", "default": "AUTO", "label": "Import Engine", "items": [
        ("AUTO", "Auto", "Blender's Python FBX importer (most options); the C++ importer is used automatically for FBX 6.x files"),
        ("PYTHON", "Python (Legacy)", "io_scene_fbx: supports bone orientation, manual axes and all classic options"),
        ("CPP", "C++ (Fast)", "Blender 4.5+ C++ importer (ufbx): much faster on big files, fewer options")],
        "description": "Which of Blender's FBX importers does the low-level import"},
    "material_mode": {"type": "enum", "default": "PROFILE", "label": "Materials", "items": [
        ("PROFILE", "From Profile", "Use the material template set by the game profile"),
        ("PBR", "PBR (Principled)", "Rebuild materials with Principled BSDF and all found textures"),
        ("TOON", "Toon (EEVEE)", "Two-tone toon approximation for EEVEE with a Principled fallback for Cycles"),
        ("FLAT", "Unlit", "Show texture colors without lighting"),
        ("KEEP", "Keep FBX Materials", "Keep the importer's materials, only repair missing texture paths"),
        ("TEMPLATE", "Template Material", "Copy a reference material and fill its image nodes by slot name")],
        "description": "How imported materials are rebuilt"},
    "template_material": {"type": "str", "default": "", "label": "Template Material",
                          "description": "Name of a material in this file used as reference (image nodes labeled base_color, normal, _MainTex, ...)"},
    "texture_storage": {"type": "enum", "default": "AUTO", "label": "Texture Storage", "items": [
        ("AUTO", "Auto", "Pack textures from archives into the .blend; reference loose files where they are"),
        ("PACK", "Pack into .blend", "Pack every imported texture into the .blend file"),
        ("COPY", "Copy to Folder", "Copy textures to a folder (relative to the .blend) and link them from there"),
        ("REFERENCE", "Reference Originals", "Keep paths to the original files (archive textures are still packed)")],
        "description": "Where imported textures live after the import"},
    "copy_dir": {"type": "str", "default": "//textures", "label": "Texture Folder", "subtype": "DIR_PATH",
                 "description": "Folder for 'Copy to Folder' (// means next to the .blend)"},
    "use_fbx_references": {"type": "bool", "default": True, "label": "FBX Texture Links",
                           "description": "Use texture files linked inside the FBX"},
    "use_material_json": {"type": "bool", "default": True, "label": "Material JSON",
                          "description": "Read Unity material JSON (Materials/<name>.json) exported by AssetStudio-style tools"},
    "name_matching": {"type": "bool", "default": True, "label": "Match by Name",
                      "description": "Find textures by material, mesh and profile naming conventions when the FBX has no links"},
    "search_depth": {"type": "int", "default": 4, "min": 0, "max": 12, "label": "Search Depth",
                     "description": "How many folder levels below the FBX are searched for textures"},
    "search_parent_levels": {"type": "int", "default": 1, "min": 0, "max": 4, "label": "Parent Levels",
                             "description": "Also search this many parent folders of a loose FBX"},
    "reuse_images": {"type": "bool", "default": True, "label": "Reuse Images",
                     "description": "Reuse images already in the file when they are the same texture"},
    #organization
    "organize": {"type": "enum", "default": "MODEL", "label": "Collections", "items": [
        ("MODEL", "Per Model", "One collection per imported model"),
        ("ARCHIVE", "Per Archive", "A collection per archive with one sub-collection per model"),
        ("NONE", "Active Collection", "Import straight into the active collection")],
        "description": "How imported objects are organized"},
    "group_by_category": {"type": "bool", "default": True, "label": "Category Sub-collections",
                          "description": "Put collision meshes and other categories in their own sub-collections"},
    "handle_lods": {"type": "bool", "default": True, "label": "Handle LODs",
                    "description": "Detect LOD meshes and hide lower detail levels (nothing is deleted)"},
    "handle_collision": {"type": "bool", "default": True, "label": "Handle Collision",
                         "description": "Detect collision meshes (UCX_, _col, ...) and show them as wireframe, hidden in renders"},
    "tag_metadata": {"type": "bool", "default": True, "label": "Store Source Info",
                     "description": "Remember the source file, profile and FBX info on the model collection"},
    #fbx data
    "global_scale": {"type": "float", "default": 1.0, "min": 0.0001, "max": 10000.0, "label": "Scale",
                     "description": "Scale applied on top of the FBX unit conversion"},
    "use_custom_normals": {"type": "bool", "default": True, "label": "Custom Normals",
                           "description": "Import the FBX normals exactly (recommended for game assets)"},
    "colors_type": {"type": "enum", "default": "SRGB", "label": "Vertex Colors", "items": [
        ("SRGB", "sRGB", "Vertex colors are sRGB (most game assets)"),
        ("LINEAR", "Linear", "Vertex colors are linear data"),
        ("NONE", "None", "Do not import vertex colors")], "description": "How FBX vertex colors are imported"},
    "keep_tangents": {"type": "bool", "default": True, "label": "Keep Tangents",
                      "description": "Store FBX tangents/binormals as corner attributes (fbx_tangent, fbx_binormal)"},
    "use_custom_props": {"type": "bool", "default": True, "label": "Custom Properties",
                         "description": "Import FBX user properties as custom properties"},
    "use_subsurf": {"type": "bool", "default": False, "label": "Subdivision Data",
                    "description": "Import FBX subdivision settings as modifiers"},
    "split_inbetweens": {"type": "bool", "default": True, "label": "Keep In-between Shapes",
                         "description": "Keep in-between shape keys (Blender's importers abort or drop them otherwise)"},
    "inbetween_drivers": {"type": "bool", "default": True, "label": "Rebuild In-between Blending",
                          "description": "Drive in-between shape keys from the original channel like FBX does"},
    #animation
    "use_anim": {"type": "bool", "default": True, "label": "Animation", "description": "Import animation clips"},
    "anim_offset": {"type": "float", "default": 1.0, "min": -10000.0, "max": 10000.0, "label": "Animation Offset",
                    "description": "Offset applied to animation keys, in frames"},
    #armature
    "ignore_leaf_bones": {"type": "bool", "default": False, "label": "Ignore Leaf Bones",
                          "description": "Skip the last bone of each chain (FBX end bones)"},
    "force_connect_children": {"type": "bool", "default": False, "label": "Force Connect Children",
                               "description": "Connect children bones to their parent (Python importer only)"},
    "automatic_bone_orientation": {"type": "bool", "default": False, "label": "Automatic Bone Orientation",
                                   "description": "Point bones at their children (changes bone axes; Python importer only)"},
    "primary_bone_axis": {"type": "enum", "default": "Y", "items": AXES, "label": "Primary Bone Axis",
                          "description": "FBX axis along the bone (Python importer only)"},
    "secondary_bone_axis": {"type": "enum", "default": "X", "items": AXES, "label": "Secondary Bone Axis",
                            "description": "FBX axis used for bone roll (Python importer only)"},
    "fit_bones": {"type": "enum", "default": "PROFILE", "label": "Fit Bone Lengths", "items": [
        ("PROFILE", "From Profile", "Fit bone lengths when the game profile asks for it"),
        ("ON", "On", "Size bones to reach their child joint (display only, deformation unchanged)"),
        ("OFF", "Off", "Keep the importer's bone lengths")],
        "description": "Resize bones so chains meet end to end (cosmetic; skinning and animation are unchanged)"},
    "verify_skinning": {"type": "bool", "default": True, "label": "Verify Skinning",
                        "description": "Check every skinned mesh is bound to its armature and repair it if not"},
    "use_prepost_rot": {"type": "bool", "default": True, "label": "Use Pre/Post Rotation",
                        "description": "Apply FBX pre/post rotations (Python importer only)"},
    #orientation
    "use_manual_orientation": {"type": "bool", "default": False, "label": "Manual Orientation",
                               "description": "Override the FBX axis settings (Python importer only)"},
    "axis_forward": {"type": "enum", "default": "-Z", "items": AXES, "label": "Forward", "description": "Forward axis"},
    "axis_up": {"type": "enum", "default": "Y", "items": AXES, "label": "Up", "description": "Up axis"},
    "bake_space_transform": {"type": "bool", "default": False, "label": "Apply Transform",
                             "description": "Bake the axis conversion into the data (experimental in Blender; Python importer only)"},
    #behaviour
    "apply_profile_import": {"type": "bool", "default": True, "label": "Profile Import Settings",
                             "description": "Let the game profile override importer settings it cares about"},
    "stop_on_error": {"type": "bool", "default": False, "label": "Stop Batch on Error",
                      "description": "Stop a batch at the first failed model instead of continuing"},
}


def defaults():
    return {k: v["default"] for k, v in OPTIONS.items()}


def coerce(name, value):
    spec = OPTIONS.get(name)
    if spec is None:
        return value
    t = spec["type"]
    try:
        if t == "bool":
            return bool(value)
        if t == "int":
            return int(value)
        if t == "float":
            return float(value)
        if t == "enum":
            ids = [i[0] for i in spec["items"]]
            return value if value in ids else spec["default"]
        return str(value)
    except (TypeError, ValueError):
        return spec["default"]
