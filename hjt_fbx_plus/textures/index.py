# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#index of every image (+ material json) we could use for an fbx, from an archive or a folder scan
# lookups r dict based so 10k+ textures stays instant
#
#the "dont pick the wrong body_d.png" logic lives in resolve_reference:
#  more matching path components from the end > closer to the fbx folder > same extension
#  real ties (2 different files, same score) get flagged as ambiguous instead of silently guessed

from __future__ import annotations

import os

from ..archives.base import IMAGE_EXTS

EXT_PREFERENCE = {".png": 6, ".tga": 5, ".dds": 4, ".tif": 3, ".tiff": 3, ".jpg": 2, ".jpeg": 2, ".bmp": 1,
                  ".exr": 3, ".webp": 2, ".psd": 1, ".hdr": 1}
SKIP_DIRS = frozenset(("__pycache__", "node_modules", ".git", ".svn", "$recycle.bin", "system volume information"))


class TexEntry:
    __slots__ = ("key", "path", "name", "name_l", "stem_l", "ext", "dir_l", "dir_parts", "size", "crc",
                 "archive_entry", "abspath", "_slot")

    def __init__(self, path, size=0, crc=None, archive_entry=None, abspath=None):
        self.path = path.replace("\\", "/")
        self.key = ("arc:" + self.path) if archive_entry is not None else (abspath or self.path)
        self.name = self.path.rsplit("/", 1)[-1]
        self.name_l = self.name.lower()
        dot = self.name_l.rfind(".")
        self.stem_l = self.name_l[:dot] if dot > 0 else self.name_l
        self.ext = self.name_l[dot:] if dot > 0 else ""
        self.dir_l = self.path.rpartition("/")[0].lower()
        self.dir_parts = tuple(p for p in self.dir_l.split("/") if p)
        self.size = size
        self.crc = crc
        self.archive_entry = archive_entry
        self.abspath = abspath
        self._slot = None

    def identity(self):
        if self.crc is not None:
            return ("crc", self.size, self.crc)
        return ("path", self.key)

    def __repr__(self):
        return "<TexEntry %s>" % self.path


def _norm_ref(ref):
    r = (ref or "").replace("\\", "/").strip()
    if len(r) >= 2 and r[1] == ":":
        r = r[2:]
    return r


def _dir_distance(a, b):
    #how far apart 2 dirs r: (steps up, steps down)
    common = 0
    for x, y in zip(a, b):
        if x != y:
            break
        common += 1
    return len(a) - common, len(b) - common


class TextureIndex:
    def __init__(self, kind):
        self.kind = kind  # "archive" or "disk"
        self.images = []
        self.jsons = []
        self.by_name = {}
        self.by_stem = {}
        self.json_by_stem = {}
        self._classified = {}

    def add(self, entry):
        if entry.ext in IMAGE_EXTS:
            self.images.append(entry)
            self.by_name.setdefault(entry.name_l, []).append(entry)
            self.by_stem.setdefault(entry.stem_l, []).append(entry)
        elif entry.ext == ".json":
            self.jsons.append(entry)
            self.json_by_stem.setdefault(entry.stem_l, []).append(entry)

    @classmethod
    def from_archive(cls, archive_index):
        idx = cls("archive")
        for e in archive_index.files:
            if e.kind in ("image", "data"):
                idx.add(TexEntry(e.path, e.size, e.crc, archive_entry=e))
        return idx

    @classmethod
    def from_disk(cls, roots, max_depth=4, max_files=60000, cancel=None):
        #bounded walk so an fbx sitting in ~/Downloads doesnt make us index the whole disk lol
        idx = cls("disk")
        seen = set()
        count = 0
        for root in roots:
            root = os.path.abspath(root)
            if not os.path.isdir(root) or root in seen:
                continue
            seen.add(root)
            stack = [(root, 0)]
            while stack and count < max_files:
                if cancel is not None and cancel.is_set():
                    return idx
                d, depth = stack.pop()
                try:
                    it = os.scandir(d)
                except OSError:
                    continue
                with it:
                    for de in it:
                        name = de.name
                        try:
                            if de.is_dir(follow_symlinks=False):
                                if depth < max_depth and not name.startswith(".") and name.lower() not in SKIP_DIRS:
                                    full = os.path.join(d, name)
                                    if full not in seen:
                                        seen.add(full)
                                        stack.append((full, depth + 1))
                                continue
                            low = name.lower()
                            dot = low.rfind(".")
                            ext = low[dot:] if dot > 0 else ""
                            if ext not in IMAGE_EXTS and ext != ".json":
                                continue
                            full = os.path.join(d, name)
                            st = de.stat()
                            idx.add(TexEntry(full.replace("\\", "/"), st.st_size, None, None, abspath=full))
                            count += 1
                        except OSError:
                            continue
        return idx

    #lookups
    def resolve_reference(self, ref, base_dir):
        #fbx texture path -> (best entry, alternatives, ambiguous?)
        r = _norm_ref(ref)
        if not r:
            return None, [], False
        parts = [p for p in r.lower().split("/") if p and p != "."]
        name = parts[-1]
        dot = name.rfind(".")
        stem = name[:dot] if dot > 0 else name
        ext = name[dot:] if dot > 0 else ""
        cands = list(self.by_name.get(name, ()))
        for e in self.by_stem.get(stem, ()):
            if e.ext != ext:
                cands.append(e)
        if not cands:
            return None, [], False
        base = tuple(p for p in base_dir.replace("\\", "/").lower().split("/") if p)
        ref_dirs = parts[:-1]
        #relative refs like "../Textures/a.png" resolved against the fbx folder
        rel_target = None
        if not r.startswith("/") and ref_dirs:
            stackp = list(base)
            for p in ref_dirs:
                if p == "..":
                    if stackp:
                        stackp.pop()
                else:
                    stackp.append(p)
            rel_target = tuple(stackp)
        scored = []
        for e in cands:
            k = 0
            for a, b in zip(reversed(ref_dirs), reversed(e.dir_parts)):
                if a != b:
                    break
                k += 1
            up, down = _dir_distance(base, e.dir_parts)
            score = 100 + 15 * min(k, 6) - 4 * up - down
            if e.ext == ext:
                score += 10
            if rel_target is not None and e.dir_parts == rel_target:
                score += 50
            if e.dir_parts == base:
                score += 5
            scored.append((score, EXT_PREFERENCE.get(e.ext, 0), e))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        best = scored[0][2]
        alts = [t[2] for t in scored[1:6]]
        ambiguous = len(scored) > 1 and scored[1][0] == scored[0][0] and scored[1][2].identity() != best.identity()
        return best, alts, ambiguous

    def find_stem(self, stem, base_dir, prefer_dirs=()):
        #texture name w/o extension (unity json refs) -> ranked list
        cands = self.by_stem.get(stem.lower(), ())
        if not cands:
            return []
        base = tuple(p for p in base_dir.replace("\\", "/").lower().split("/") if p)
        pref = [tuple(p for p in d.lower().split("/") if p) for d in prefer_dirs]
        scored = []
        for e in cands:
            up, down = _dir_distance(base, e.dir_parts)
            score = -4 * up - down
            if e.dir_parts in pref:
                score += 20
            scored.append((score, EXT_PREFERENCE.get(e.ext, 0), e))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [t[2] for t in scored]

    def json_for_material(self, material_name, base_dir):
        cands = self.json_by_stem.get(material_name.lower(), ())
        if not cands:
            return None
        base = tuple(p for p in base_dir.replace("\\", "/").lower().split("/") if p)
        best = None
        best_score = None
        for e in cands:
            up, down = _dir_distance(base, e.dir_parts)
            score = -4 * up - down
            if e.dir_parts[-1:] == ("materials",):
                score += 10
            if best_score is None or score > best_score:
                best, best_score = e, score
        return best

    def classified(self, rules):
        #every image run through the slot rules once, cached per rule set -> {base: [(slot, entry)]}
        key = id(rules)
        hit = self._classified.get(key)
        if hit is not None:
            return hit
        from .slots import classify_name
        by_base = {}
        for e in self.images:
            slot, base = classify_name(e.stem_l, rules)
            by_base.setdefault(base, []).append((slot, e))
        self._classified[key] = by_base
        return by_base

    def stats(self):
        return {"images": len(self.images), "jsons": len(self.jsons)}
