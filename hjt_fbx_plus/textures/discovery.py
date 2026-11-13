# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#figures out which texture goes in which slot for every material of a model
#
#sources in order of trust:
#  1 fbx texture links (+ embedded media)        -> faithful
#  2 unity material json next to the fbx          -> faithful (its the actual game material)
#  3 profile name templates (Mat_Body -> Tex_Body) -> inferred
#  4 generic name matching (material/mesh names)   -> inferred
#every assignment remembers where it came from, so the ui + report can show faithful vs guessed

from __future__ import annotations

import os
import re

from .slots import SLOTS, classify_name
from .unity import UnityMaterial, read_json_bytes

CORE_SLOTS = ("base_color", "normal", "metallic", "roughness", "smoothness", "specular", "ao", "emission",
              "emission_mask", "opacity", "orm", "mra", "rma", "metallic_smoothness", "hdrp_mask")
FAITHFUL_SOURCES = ("fbx", "embedded", "unity-json")
_DUP = re.compile(r"\.\d{3}$")
_UE_PLACEHOLDER = re.compile(r"(?i)^(t_)?(default|srgb_|linear_)|^(t_srgb_|t_linear_|t_flat|t_white|t_black|t_gray|t_grey)|noise|tonemap|lut$")


class Assignment:
    __slots__ = ("slot", "tex", "source", "confidence", "unity_slot", "scale", "offset", "fbx_property",
                 "alternatives", "note", "uv_set", "ambiguous")

    def __init__(self, slot, tex, source, confidence=1.0, unity_slot="", scale=(1.0, 1.0), offset=(0.0, 0.0),
                 fbx_property="", alternatives=(), note="", uv_set="", ambiguous=False):
        self.slot = slot
        self.tex = tex
        self.source = source
        self.confidence = confidence
        self.unity_slot = unity_slot
        self.scale = scale
        self.offset = offset
        self.fbx_property = fbx_property
        self.alternatives = list(alternatives)
        self.note = note
        self.uv_set = uv_set
        self.ambiguous = ambiguous

    @property
    def faithful(self):
        return self.source in FAITHFUL_SOURCES

    def as_dict(self):
        return {"slot": self.slot, "file": self.tex.path if self.tex else "", "source": self.source,
                "confidence": round(self.confidence, 2), "unity_slot": self.unity_slot,
                "fbx_property": self.fbx_property, "ambiguous": self.ambiguous, "note": self.note,
                "alternatives": [a.path for a in self.alternatives[:4]]}


class MaterialPlan:
    __slots__ = ("name", "uid", "category", "settings", "assignments", "params", "shader", "json_path",
                 "warnings", "missing", "users", "fbx_props", "alpha_from_base")

    def __init__(self, name, uid):
        self.name = name
        self.uid = uid
        self.category = ""
        self.settings = {}
        self.assignments = []
        self.params = {}
        self.shader = ""
        self.json_path = ""
        self.warnings = []
        self.missing = []
        self.users = []
        self.fbx_props = {}
        self.alpha_from_base = False

    def get(self, slot):
        for a in self.assignments:
            if a.slot == slot:
                return a
        return None

    def has(self, slot):
        return self.get(slot) is not None

    def as_dict(self):
        return {"name": self.name, "category": self.category, "shader": self.shader, "json": self.json_path,
                "template": self.settings.get("template"), "assignments": [a.as_dict() for a in self.assignments],
                "missing": list(self.missing), "warnings": list(self.warnings)}


_TOKEN_JUNK = {"mi", "m", "t", "mat", "d", "n"}


def _tokens(name):
    return {t for t in re.split(r"[^0-9a-z]+", (name or "").lower()) if t and t not in _TOKEN_JUNK}


def _basename(p):
    return (p or "").replace("\\", "/").rsplit("/", 1)[-1]


def decide_slot(fbx_hint, stem, rules):
    #fbx says one thing, the filename says another. rippers love plugging everything into DiffuseColor,
    # so a clear filename suffix wins over a DiffuseColor link
    cls_slot, _base = classify_name(stem, rules)
    if not fbx_hint:
        return cls_slot or "aux", ""
    if cls_slot and cls_slot != fbx_hint:
        if fbx_hint == "base_color":
            return cls_slot, "linked as Diffuse but named like %s" % SLOTS[cls_slot]["label"]
        if fbx_hint == "normal" and cls_slot == "height":
            return "height", "linked as Bump but named like a height map"
    return fbx_hint, ""


class _Planner:
    def __init__(self, summary, index, profile, base_dir, embedded, read_json, log):
        self.summary = summary
        self.index = index
        self.profile = profile
        self.base_dir = base_dir
        self.embedded = embedded or {}
        self.read_json = read_json
        self.log = log
        self.rules = profile.slot_rules
        self.classified = index.classified(self.rules)
        self.tex_opts = profile.data["textures"]

    def plan(self):
        plans = []
        mats = self.summary.get("materials", [])
        for m in mats:
            plans.append(self.plan_material(m, len(mats)))
        return plans

    #helpers
    def _add(self, plan, a, prefer_new=False):
        #one primary assignment per slot. aux slots can stack (diff unity slots)
        for i, old in enumerate(plan.assignments):
            if old.slot != a.slot:
                continue
            if SLOTS.get(a.slot, {}).get("aux") and (a.unity_slot or old.unity_slot) and a.unity_slot != old.unity_slot:
                continue
            if old.tex is not None and a.tex is not None and old.tex.identity() == a.tex.identity():
                if a.faithful and not old.faithful:
                    plan.assignments[i] = a
                return
            if prefer_new:
                a.alternatives = [old.tex] + a.alternatives
                plan.assignments[i] = a
            else:
                old.alternatives.append(a.tex)
            return
        plan.assignments.append(a)

    def _closest(self, entries):
        base = tuple(p for p in self.base_dir.lower().split("/") if p)
        best = None
        for e in entries:
            common = 0
            for x, y in zip(base, e.dir_parts):
                if x != y:
                    break
                common += 1
            score = -4 * (len(base) - common) - (len(e.dir_parts) - common)
            if best is None or score > best[0]:
                best = (score, e)
        if best is None:
            return None, False
        ties = [e for e in entries if e is not best[1] and e.identity() != best[1].identity()]
        tied = False
        for e in ties:
            common = 0
            for x, y in zip(base, e.dir_parts):
                if x != y:
                    break
                common += 1
            if -4 * (len(base) - common) - (len(e.dir_parts) - common) == best[0]:
                tied = True
                break
        return best[1], tied

    def _by_base(self, plan, base, source, confidence, only_slots=None, note=""):
        found = 0
        lst = self.classified.get(base.lower())
        if not lst:
            return 0
        by_slot = {}
        for slot, e in lst:
            if slot is None:
                slot = self.tex_opts.get("bare_name_slot", "base_color")
                if not slot:
                    continue
            by_slot.setdefault(slot, []).append(e)
        for slot, entries in by_slot.items():
            if only_slots and slot not in only_slots:
                continue
            if plan.has(slot) and not SLOTS.get(slot, {}).get("aux"):
                continue
            best, tied = self._closest(entries)
            if best is None:
                continue
            alts = [e for e in entries if e is not best]
            self._add(plan, Assignment(slot, best, source, confidence, alternatives=alts, ambiguous=tied, note=note))
            if tied:
                plan.warnings.append("Ambiguous %s for '%s': picked %s over %s" % (
                    SLOTS[slot]["label"], plan.name, best.path, ", ".join(a.path for a in alts[:2])))
            found += 1
        return found

    #main
    def plan_material(self, m, material_count):
        p = MaterialPlan(m["name"], m.get("uid", ""))
        p.users = list(m.get("users", []))
        p.fbx_props = m.get("props", {})
        clean = _DUP.sub("", p.name)
        um = None
        if self.tex_opts.get("use_material_json", True) and self.read_json is not None:
            je = self.index.json_for_material(clean, self.base_dir)
            if je is not None:
                try:
                    data = read_json_bytes(self.read_json(je))
                    um = UnityMaterial.from_json(data, je.path)
                except Exception as exc:  # noqa: BLE001
                    p.warnings.append("Could not read material JSON %s: %s" % (je.path, exc))
                if um is not None:
                    p.json_path = je.path
                    p.shader = um.shader
                    p.params = um.params()
        p.settings, p.category = self.profile.material_settings(p.name, p.shader, p.users)

        #1 fbx links
        if self.tex_opts.get("use_fbx_references", True):
            for t in m.get("textures", []):
                ref = t.get("relative") or t.get("filename") or ""
                tex = alts = None
                amb = False
                source = "fbx"
                if t.get("embedded") and t.get("video_uid") in self.embedded:
                    tex = self.embedded[t["video_uid"]]
                    alts = []
                    source = "embedded"
                else:
                    tex, alts, amb = self.index.resolve_reference(ref, self.base_dir)
                    if tex is None and t.get("filename") and t.get("filename") != ref:
                        tex, alts, amb = self.index.resolve_reference(t["filename"], self.base_dir)
                if tex is None:
                    if ref:
                        p.missing.append({"slot": t.get("slot_hint") or "?", "name": _basename(ref), "source": "fbx",
                                          "property": t.get("property", "")})
                    continue
                slot, note = decide_slot(t.get("slot_hint"), tex.stem_l, self.rules)
                if t.get("property") in ("TransparentColor", "TransparencyFactor") and slot == "opacity":
                    for other in m.get("textures", []):
                        if other is not t and _basename(other.get("relative") or other.get("filename")) == _basename(ref) and other.get("slot_hint") == "base_color":
                            p.alpha_from_base = True
                            slot = None
                            break
                    if slot is None:
                        continue
                a = Assignment(slot, tex, source, 1.0, fbx_property=t.get("property", ""), alternatives=alts or [],
                               note=note, uv_set=t.get("uv_set", ""), ambiguous=amb,
                               scale=tuple(t.get("scaling", (1.0, 1.0, 1.0))[:2]),
                               offset=tuple(t.get("translation", (0.0, 0.0, 0.0))[:2]))
                if amb:
                    p.warnings.append("Ambiguous texture '%s' for '%s': picked %s" % (_basename(ref), p.name, tex.path))
                self._add(p, a)

        #2 unity json
        if um is not None:
            overrides = p.settings.get("unity_slots") or {}
            json_dir = um.source.rpartition("/")[0]
            prefer = [json_dir.rpartition("/")[0] + "/Textures", self.base_dir + "/Textures", json_dir]
            items = list(um.textures.items())
            if um.kind == "ue":
                #several params fight over one slot (MainTex vs PM_Diffuse vs D). fmodels PM_* r its own
                # guesses n love picking a 1x1 white or a star fx. the texture named like the material wins,
                # PM_* loses ties. sorted worst -> best bc the json adds w prefer_new, last one stays
                mtok = _tokens(clean)
                items.sort(key=lambda kv: (len(mtok & _tokens(kv[1]["name"])), not kv[0].startswith("PM_")))
            for uslot, info in items:
                slot = overrides.get(uslot) or self.profile.unity_slot(uslot)
                #ue instances list every param of the parent shader: noise, dissolve, luts, 1x1 defaults...
                # unknown params only stay when the filename says what they r
                if um.kind == "ue" and _UE_PLACEHOLDER.search(info["name"]):
                    continue
                if um.kind == "ue" and slot == "aux":
                    slot, _base = classify_name(info["name"].lower(), self.rules)
                    if not slot or SLOTS.get(slot, {}).get("aux") and slot not in ("lightmap", "ramp", "face_map", "matcap", "mask"):
                        continue
                cands = self.index.find_stem(info["name"], self.base_dir, prefer)
                if not cands:
                    p.missing.append({"slot": slot, "name": info["name"], "source": "unity-json", "property": uslot})
                    continue
                self._add(p, Assignment(slot, cands[0], "unity-json", 1.0, unity_slot=uslot, scale=info["scale"],
                                        offset=info["offset"], alternatives=cands[1:4]),
                          prefer_new=self.tex_opts.get("prefer_material_json", True))

        #3 profile templates
        for rx, bases, only in self.profile.name_templates:
            mt = rx.search(clean)
            if not mt:
                continue
            groups = {k: (v or "") for k, v in mt.groupdict().items()}
            hit = 0
            for fmt in bases:
                try:
                    base = fmt.format(**groups)
                except (KeyError, IndexError):
                    continue
                hit += self._by_base(p, base, "profile-name", 0.85, only)
            part = groups.get("part", "").lower()
            if not hit and part and part in self.profile.part_fallbacks:
                for alt in self.profile.part_fallbacks[part]:
                    g2 = dict(groups)
                    g2["part"] = alt.capitalize()
                    for fmt in bases:
                        try:
                            base = fmt.format(**g2)
                        except (KeyError, IndexError):
                            continue
                        n = self._by_base(p, base, "profile-name", 0.5, only,
                                          note="no %s textures; using the %s part (guessed)" % (groups.get("part"), alt))
                        if n:
                            p.warnings.append("'%s' has no own textures; using %s textures (guessed, please verify)" % (p.name, alt))
                            break
                    if p.has("base_color"):
                        break

        #4 generic name matching
        if self.tex_opts.get("name_matching", True) and not p.has("base_color"):
            keys = [self.profile.strip_material_name(clean)]
            keys += [self.profile.strip_material_name(u) for u in p.users]
            for k in keys:
                if k and self._by_base(p, k, "name-match", 0.7):
                    break
            if not p.has("base_color"):
                #partial: texture base ends w the material key or the other way round (Body vs Char_Body)
                k = keys[0] if keys else ""
                if len(k) >= 3:
                    for base, lst in self.classified.items():
                        if not base or base == k:
                            continue
                        if base.endswith("_" + k) or k.endswith("_" + base) or base.endswith(k) and len(k) >= 5:
                            if self._by_base(p, base, "name-match", 0.5):
                                break
        #5 single material model, single colour texture next to it. last resort
        if not p.has("base_color") and material_count == 1 and self.tex_opts.get("name_matching", True):
            base_dir = tuple(x for x in self.base_dir.lower().split("/") if x)
            colors = [e for b, lst in self.classified.items() for s, e in lst if s == "base_color" and e.dir_parts[:len(base_dir)] == base_dir]
            if len(colors) == 1:
                self._add(p, Assignment("base_color", colors[0], "name-match", 0.4,
                                        note="only color texture next to the model"))
                p.warnings.append("'%s': used the only color texture found (%s), low confidence" % (p.name, colors[0].path))

        if not p.has("base_color"):
            had_refs = bool(m.get("textures")) or (um is not None and um.textures)
            if had_refs or um is None:
                p.warnings.append("No base color texture found for '%s'" % p.name)
        return p


def plan_materials(summary, index, profile, base_dir, embedded=None, read_json=None, log=None):
    #summary = fbx inspect summary, index = TextureIndex, base_dir = fbx folder (archive member dir or abs path)
    return _Planner(summary, index, profile, base_dir.replace("\\", "/"), embedded, read_json, log).plan()


def required_entries(plans):
    #every archive/disk entry the plans need (so the pipeline knows what to extract)
    out = {}
    for p in plans:
        for a in p.assignments:
            if a.tex is not None:
                out[a.tex.key] = a.tex
    return list(out.values())
