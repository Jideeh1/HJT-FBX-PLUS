# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#profile format + defaults + validation. profiles r plain json so ppl can share/edit them
# anything missing from a profile falls back to the defaults below

from __future__ import annotations

import copy
import re

from ..textures.slots import DEFAULT_SLOT_RULES, DEFAULT_UNITY_SLOTS, SLOTS, compile_rules

SCHEMA_VERSION = 1

TEMPLATES = ("PBR", "TOON", "FLAT", "KEEP", "TEMPLATE")
ALPHA_MODES = ("AUTO", "OPAQUE", "CLIP", "HASHED", "BLEND")
ALPHA_SOURCES = ("AUTO", "BASE_ALPHA", "OPACITY_MAP", "NONE")
BASE_ALPHA_USAGES = ("AUTO", "NONE", "TRANSPARENCY", "EMISSION_MASK")
CULLING = ("AUTO", "ON", "OFF")
VERTEX_COLOR = ("NONE", "MULTIPLY", "AUTO")
NORMAL_FORMATS = ("AUTO", "RGB", "RG", "AG")
NORMAL_CONVENTIONS = ("OPENGL", "DIRECTX")
AO_MODES = ("AUX", "MULTIPLY")
LOD_ACTIONS = ("HIDE_LOWER", "COLLECTIONS", "KEEP")
COLLISION_ACTIONS = ("WIRE_HIDE", "HIDE", "KEEP")
CUSTOM_ACTIONS = ("HIDE", "WIRE", "COLLECTION", "KEEP")

DEFAULT_MATERIAL = {
    "template": "PBR",
    "template_material": "",
    "alpha_mode": "AUTO",
    "alpha_source": "AUTO",
    "alpha_threshold": 0.5,
    "backface_culling": "AUTO",
    "vertex_color": "NONE",
    "normal_format": "AUTO",
    "normal_convention": "OPENGL",
    "normal_strength": 1.0,
    "ao_mode": "AUX",
    "emission_strength": 1.0,
    "base_alpha_usage": "AUTO",
    "roughness": None,
    "metallic": None,
    "specular": None,
    "shadow_tint": [0.78, 0.74, 0.84],
    "shadow_threshold": 0.5,
    "shadow_feather": 0.02,
    "use_material_params": True,
}

DEFAULT_PROFILE = {
    "schema": SCHEMA_VERSION,
    "id": "generic",
    "name": "Generic FBX",
    "description": "",
    "author": "",
    "version": "1.0",
    "detect": {"min_score": 0.0, "rules": []},
    "import": {},
    "textures": {
        "slots": [],
        "replace_default_slots": False,
        "unity_slots": {},
        "name_templates": [],
        "strip_material_tokens": [r"(?i)^(?:mat|mtl|mi|material)[_\- ]+", r"(?i)[_\- ]+(?:mat|mtl|material)$",
                                  r"\.\d{3}$"],
        "part_fallbacks": {},
        "use_fbx_references": True,
        "use_material_json": True,
        "prefer_material_json": True,
        "name_matching": True,
        "search_depth": 4,
        "search_parent_levels": 1,
    },
    "materials": {"default": dict(DEFAULT_MATERIAL), "rules": []},
    "categories": {
        "lod": {"enabled": True, "action": "HIDE_LOWER",
                "patterns": [r"(?i)(?:^|[_\-. ])lod[_\-. ]?(\d+)(?:$|[_\-. ])"]},
        "collision": {"enabled": True, "action": "WIRE_HIDE",
                      "patterns": [r"^(?:UCX|UBX|UCP|USP|UCY|UME)_",
                                   r"(?i)(?:^|[_\-. ])(?:col|coll|collision|collider|collisionmesh|phys|physics|hitbox)(?:$|[_\-. ]|\d)"]},
        "custom": [],
    },
    "corrections": {"fit_bone_lengths": False, "verify_skinning": True},
}

#import keys a profile may override (scene settings with the same names)
IMPORT_KEYS = {
    "engine": str, "global_scale": float, "use_custom_normals": bool, "colors_type": str, "use_anim": bool,
    "anim_offset": float, "ignore_leaf_bones": bool, "force_connect_children": bool,
    "automatic_bone_orientation": bool, "primary_bone_axis": str, "secondary_bone_axis": str,
    "use_prepost_rot": bool, "use_custom_props": bool, "use_subsurf": bool, "keep_tangents": bool,
    "inbetween_drivers": bool,
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _check_regex(pat, where, errors):
    try:
        re.compile(pat)
        return True
    except re.error as exc:
        errors.append("%s: bad regex %r (%s)" % (where, pat, exc))
        return False


def validate(data):
    #-> list of human readable problems. empty = fine
    errors = []
    if not isinstance(data, dict):
        return ["profile must be a JSON object"]
    if not isinstance(data.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", data.get("id") or ""):
        errors.append("id must be 1-64 letters, digits, '_' or '-'")
    if not isinstance(data.get("name"), str) or not data.get("name"):
        errors.append("name is required")
    tex = data.get("textures", {})
    for i, r in enumerate(tex.get("slots", [])):
        if not isinstance(r, dict) or r.get("slot") not in SLOTS:
            errors.append("textures.slots[%d]: unknown slot %r" % (i, (r or {}).get("slot") if isinstance(r, dict) else r))
            continue
        _check_regex(r.get("pattern", ""), "textures.slots[%d]" % i, errors)
    for k, v in (tex.get("unity_slots") or {}).items():
        if v not in SLOTS:
            errors.append("textures.unity_slots[%s]: unknown slot %r" % (k, v))
    for i, t in enumerate(tex.get("name_templates", [])):
        if not isinstance(t, dict) or "material" not in t:
            errors.append("textures.name_templates[%d]: needs 'material' regex" % i)
            continue
        _check_regex(t["material"], "textures.name_templates[%d].material" % i, errors)
    for i, p in enumerate(tex.get("strip_material_tokens", [])):
        _check_regex(p, "textures.strip_material_tokens[%d]" % i, errors)
    mats = data.get("materials", {})
    for i, r in enumerate(mats.get("rules", [])):
        if not isinstance(r, dict) or "match" not in r:
            errors.append("materials.rules[%d]: needs 'match' regex" % i)
            continue
        _check_regex(r["match"], "materials.rules[%d].match" % i, errors)
    for section in [mats.get("default", {})] + [r.get("settings", {}) for r in mats.get("rules", []) if isinstance(r, dict)]:
        for key, allowed in (("template", TEMPLATES), ("alpha_mode", ALPHA_MODES), ("alpha_source", ALPHA_SOURCES),
                             ("base_alpha_usage", BASE_ALPHA_USAGES), ("backface_culling", CULLING),
                             ("vertex_color", VERTEX_COLOR), ("normal_format", NORMAL_FORMATS),
                             ("normal_convention", NORMAL_CONVENTIONS), ("ao_mode", AO_MODES)):
            if key in section and section[key] not in allowed:
                errors.append("materials: %s must be one of %s (got %r)" % (key, ", ".join(allowed), section[key]))
    cats = data.get("categories", {})
    for key in ("lod", "collision"):
        for i, p in enumerate((cats.get(key) or {}).get("patterns", [])):
            _check_regex(p, "categories.%s.patterns[%d]" % (key, i), errors)
    for i, c in enumerate(cats.get("custom", [])):
        if not isinstance(c, dict) or "pattern" not in c or "name" not in c:
            errors.append("categories.custom[%d]: needs 'name' and 'pattern'" % i)
            continue
        _check_regex(c["pattern"], "categories.custom[%d]" % i, errors)
        if c.get("action", "KEEP") not in CUSTOM_ACTIONS:
            errors.append("categories.custom[%d]: action must be one of %s" % (i, ", ".join(CUSTOM_ACTIONS)))
    for i, r in enumerate((data.get("detect") or {}).get("rules", [])):
        if not isinstance(r, dict) or "pattern" not in r:
            errors.append("detect.rules[%d]: needs 'pattern'" % i)
            continue
        _check_regex(r["pattern"], "detect.rules[%d]" % i, errors)
    for k in (data.get("import") or {}):
        if k not in IMPORT_KEYS:
            errors.append("import: unknown setting %r" % k)
    return errors


class Profile:
    #compiled view of a profile json. compile once, use a lot
    def __init__(self, data, path=None, builtin=False):
        self.raw = copy.deepcopy(data)
        self.data = _merge(DEFAULT_PROFILE, data)
        d = self.data
        self.id = d["id"]
        self.name = d["name"]
        self.description = d.get("description", "")
        self.path = path
        self.builtin = builtin
        tex = d["textures"]
        own = [(r["slot"], r["pattern"]) for r in tex.get("slots", []) if isinstance(r, dict) and r.get("slot") in SLOTS]
        rules = own if tex.get("replace_default_slots") else own + list(DEFAULT_SLOT_RULES)
        self.slot_rules = compile_rules(rules)
        self.unity_slots = dict(DEFAULT_UNITY_SLOTS)
        self.unity_slots.update(tex.get("unity_slots") or {})
        self.strip_tokens = [re.compile(p) for p in tex.get("strip_material_tokens", []) if _ok(p)]
        self.name_templates = []
        for t in tex.get("name_templates", []):
            if isinstance(t, dict) and _ok(t.get("material", "")):
                self.name_templates.append((re.compile(t["material"]), list(t.get("textures", [])), t.get("slots")))
        self.part_fallbacks = {k.lower(): [x.lower() for x in v] for k, v in (tex.get("part_fallbacks") or {}).items()}
        mats = d["materials"]
        self.material_default = _merge(DEFAULT_MATERIAL, mats.get("default", {}))
        self.material_rules = []
        for r in mats.get("rules", []):
            if isinstance(r, dict) and _ok(r.get("match", "")):
                self.material_rules.append((re.compile(r["match"]), r.get("settings", {}), r.get("category", ""),
                                            r.get("target", "material")))
        self.categories = d["categories"]
        self.lod_patterns = [re.compile(p) for p in (self.categories.get("lod") or {}).get("patterns", []) if _ok(p)]
        self.collision_patterns = [re.compile(p) for p in (self.categories.get("collision") or {}).get("patterns", []) if _ok(p)]
        self.custom_categories = [(c["name"], re.compile(c["pattern"]), c.get("action", "KEEP"), c.get("target", "object"))
                                  for c in self.categories.get("custom", []) if isinstance(c, dict) and _ok(c.get("pattern", ""))]
        self.corrections = d.get("corrections", {})
        det = d.get("detect") or {}
        self.detect_min = float(det.get("min_score", 0.0))
        self.detect_rules = [(r.get("target", "any"), re.compile(r["pattern"]), float(r.get("weight", 1.0)))
                             for r in det.get("rules", []) if isinstance(r, dict) and _ok(r.get("pattern", ""))]
        self.import_overrides = {k: v for k, v in (d.get("import") or {}).items() if k in IMPORT_KEYS}

    def material_settings(self, name, shader="", mesh_names=()):
        #merged settings for one material + its category label
        settings = dict(self.material_default)
        category = ""
        for rx, over, cat, target in self.material_rules:
            hay = [name] if target == "material" else ([shader] if target == "shader" else list(mesh_names))
            if any(rx.search(h or "") for h in hay):
                settings.update(over)
                if cat:
                    category = cat
        return settings, category

    def strip_material_name(self, name):
        out = name
        for rx in self.strip_tokens:
            out = rx.sub("", out)
        return out.strip().lower()

    def unity_slot(self, prop):
        return self.unity_slots.get(prop, "aux")

    def to_json(self):
        return copy.deepcopy(self.raw)


def _ok(pat):
    try:
        re.compile(pat)
        return True
    except (re.error, TypeError):
        return False
