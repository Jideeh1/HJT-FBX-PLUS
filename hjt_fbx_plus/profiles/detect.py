# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#guesses which game an fbx came from by throwing its names at every profiles detect rules
# each rule counts once (its weight) if ANY name matches. highest score over min_score wins

from __future__ import annotations


def haystacks(summary, nearby_textures=(), shaders=(), path=""):
    hdr = summary.get("header", {}) if summary else {}
    tex = list(summary.get("texture_files", [])) if summary else []
    tex += [t for t in nearby_textures]
    tex = [t.rsplit(".", 1)[0] if "." in t else t for t in tex]
    return {
        "material": list(summary.get("material_names", [])) if summary else [],
        "texture": tex,
        "mesh": list(summary.get("object_names", [])) if summary else [],
        "bone": list(summary.get("bone_names", []))[:2000] if summary else [],
        "creator": [hdr.get("creator", ""), hdr.get("application", ""), hdr.get("vendor", "")],
        "file": [path or ""],
        "shader": list(shaders),
    }


def score_profile(profile, hay):
    score = 0.0
    hits = []
    for target, rx, weight in profile.detect_rules:
        pool = hay.get(target)
        if pool is None:  #"any"
            pool = [x for lst in hay.values() for x in lst]
        for h in pool:
            if h and rx.search(h):
                score += weight
                hits.append(rx.pattern)
                break
    return score, hits


def detect(registry, summary, nearby_textures=(), shaders=(), path=""):
    #-> (profile id, {id: score}, hits for the winner)
    hay = haystacks(summary, nearby_textures, shaders, path)
    scores = {}
    best = ("generic", 0.0, [])
    for p in registry.ordered():
        if p.id == "generic" or not p.detect_rules:
            continue
        s, hits = score_profile(p, hay)
        scores[p.id] = s
        if s >= max(p.detect_min, 0.001) and s > best[1]:
            best = (p.id, s, hits)
    return best[0], scores, best[2]
