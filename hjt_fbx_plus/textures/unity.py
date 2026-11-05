# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#reads unity material json dumps (assetstudio / animestudio / trident style)
# these r the REAL game material: which texture goes in which slot + the float/color params
# so when they exist they beat any filename guessing. texture assignment from here = faithful
#
# 2 layouts in the wild: m_TexEnvs as a dict {slot: {...}} or as a list of {Key/first, Value/second}

from __future__ import annotations

import json


def _kv(container):
    #normalizes dict or list-of-pairs into a dict. old assetstudio uses lists, smh
    if isinstance(container, dict):
        return container
    out = {}
    if isinstance(container, list):
        for item in container:
            if not isinstance(item, dict):
                continue
            k = item.get("Key", item.get("first", item.get("key")))
            v = item.get("Value", item.get("second", item.get("value")))
            if k is not None:
                out[str(k)] = v
    return out


def _vec2(d, default):
    if not isinstance(d, dict):
        return default
    x = d.get("X", d.get("x"))
    y = d.get("Y", d.get("y"))
    try:
        return (float(x), float(y))
    except (TypeError, ValueError):
        return default


def _color(d):
    if isinstance(d, dict):
        try:
            return tuple(float(d.get(k, d.get(k.upper(), 1.0 if k == "a" else 0.0))) for k in ("r", "g", "b", "a"))
        except (TypeError, ValueError):
            return None
    if isinstance(d, (list, tuple)) and len(d) >= 3:
        try:
            vals = [float(x) for x in d[:4]]
            return tuple(vals + [1.0] * (4 - len(vals)))
        except (TypeError, ValueError):
            return None
    return None


def read_json_bytes(data):
    #utf8 w/ or w/o bom, utf16, latin1. whatever the ripper felt like that day
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return json.loads(data.decode(enc))
        except (UnicodeDecodeError, ValueError):
            continue
    return None


class UnityMaterial:
    __slots__ = ("name", "shader", "textures", "floats", "colors", "keywords", "source")

    def __init__(self):
        self.name = ""
        self.shader = ""
        self.textures = {}  #unity slot -> {"name", "scale", "offset"}
        self.floats = {}
        self.colors = {}
        self.keywords = set()
        self.source = ""

    @classmethod
    def from_json(cls, data, source=""):
        if not isinstance(data, dict):
            return None
        props = data.get("m_SavedProperties")
        if not isinstance(props, dict):
            return None
        um = cls()
        um.source = source
        um.name = str(data.get("m_Name") or data.get("Name") or "")
        sh = data.get("m_Shader")
        if isinstance(sh, dict):
            um.shader = str(sh.get("Name") or sh.get("m_Name") or "")
        for slot, val in _kv(props.get("m_TexEnvs")).items():
            if not isinstance(val, dict):
                continue
            tex = val.get("m_Texture") or {}
            if not isinstance(tex, dict) or tex.get("IsNull"):
                continue
            name = tex.get("Name") or tex.get("m_Name") or ""
            if not name:
                continue
            um.textures[slot] = {
                "name": str(name),
                "scale": _vec2(val.get("m_Scale"), (1.0, 1.0)),
                "offset": _vec2(val.get("m_Offset"), (0.0, 0.0)),
            }
        for k, v in _kv(props.get("m_Floats")).items():
            try:
                um.floats[k] = float(v)
            except (TypeError, ValueError):
                pass
        for k, v in _kv(props.get("m_Ints")).items():
            try:
                um.floats.setdefault(k, float(v))
            except (TypeError, ValueError):
                pass
        for k, v in _kv(props.get("m_Colors")).items():
            c = _color(v)
            if c is not None:
                um.colors[k] = c
        kw = data.get("m_ShaderKeywords") or data.get("m_ValidKeywords") or []
        if isinstance(kw, str):
            um.keywords = set(kw.split())
        elif isinstance(kw, list):
            um.keywords = set(str(k) for k in kw)
        return um

    #interpretation, turns unity params into stuff the material builder understands
    def params(self):
        f = self.floats
        c = self.colors
        out = {"shader": self.shader}
        tint = c.get("_Color") or c.get("_BaseColor") or c.get("_MainColor")
        if tint is not None:
            out["tint"] = tint
        blend = None
        src, dst = f.get("_SrcBlend"), f.get("_DstBlend")
        if src is not None and dst is not None:
            pair = (int(src), int(dst))
            if pair == (1, 0):
                blend = "OPAQUE"
            elif pair in ((5, 10), (1, 10)):
                blend = "BLEND"
            elif pair in ((1, 1), (5, 1)):
                blend = "ADD"
            elif pair in ((2, 0), (0, 3)):
                blend = "MULTIPLY"
        mode = f.get("_Mode")
        if mode is not None and blend is None:
            blend = {0: "OPAQUE", 1: "CLIP", 2: "BLEND", 3: "BLEND"}.get(int(mode))
        if f.get("_Surface") == 1.0 and blend in (None, "OPAQUE"):
            blend = "BLEND"
        if (f.get("_AlphaClip") == 1.0 or f.get("_ALPHATEST") == 1.0 or "_ALPHATEST_ON" in self.keywords) and blend in (None, "OPAQUE"):
            blend = "CLIP"
        if ("_ALPHABLEND_ON" in self.keywords or "_ALPHAPREMULTIPLY_ON" in self.keywords) and blend in (None, "OPAQUE"):
            blend = "BLEND"
        if blend:
            out["blend"] = blend
        if "_Cutoff" in f:
            out["alpha_threshold"] = f["_Cutoff"]
        cull = f.get("_CullMode", f.get("_Cull", f.get("_CullModeForward")))
        if cull is not None:
            out["cull"] = {0: "NONE", 1: "FRONT", 2: "BACK"}.get(int(cull), "BACK")
        if "_BumpScale" in f:
            out["normal_strength"] = f["_BumpScale"]
        sh = self.shader.lower()
        standard = any(t in sh for t in ("standard", "universal render pipeline/lit", "hdrp/lit", "autodesk interactive", "/lit"))
        if standard:
            if "_Metallic" in f:
                out["metallic"] = f["_Metallic"]
            sm = f.get("_Glossiness", f.get("_Smoothness"))
            if sm is not None:
                out["roughness"] = 1.0 - sm
            if "_EmissionColor" in c and ("_EMISSION" in self.keywords or "_EmissionMap" in self.textures):
                out["emission_color"] = c["_EmissionColor"]
        #toon-ish shadow params, every studio names these differently ofc
        for key in ("_DarkTint", "_ShadowColor", "_1st_ShadeColor", "_ShadeColor", "_ShadowTint", "_FirstShadowMultColor"):
            if key in c:
                out["shadow_tint"] = c[key]
                break
        for key in ("_ShadowThreshold", "_BaseColor_Step", "_ShadowArea", "_LightArea"):
            if key in f:
                out["shadow_threshold"] = f[key]
                break
        for key in ("_ShadowFeather", "_BaseShade_Feather", "_ShadowSmooth", "_ShadowFeatherSize"):
            if key in f:
                out["shadow_feather"] = f[key]
                break
        return out
