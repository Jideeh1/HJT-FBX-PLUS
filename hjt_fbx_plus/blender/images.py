# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#image loading w reuse. the same texture used by 5 materials (or 5 imports) = 1 image datablock
# identity = archive size+crc or file path, stored on the image so later imports find it again

from __future__ import annotations

import filecmp
import os
import shutil

import bpy

from . import compat

IDENTITY_PROP = "hjt_identity"
SOURCE_PROP = "hjt_source"


def _ident_str(tex):
    ident = tex.identity()
    if ident[0] == "crc":
        return "crc:%d:%08x" % (ident[1], ident[2] & 0xFFFFFFFF)
    return "path:" + os.path.normcase(os.path.abspath(tex.abspath or tex.path))


class ImageCache:
    def __init__(self, reuse_existing=True):
        self.by_key = {}
        self.loaded = []  #images WE created this run (for storage handling + cleanup)
        self.local_paths = {}
        if reuse_existing:
            for img in bpy.data.images:
                ident = img.get(IDENTITY_PROP)
                if ident:
                    cs = img.colorspace_settings.name
                    self.by_key.setdefault((ident, cs, img.alpha_mode), img)

    def get(self, tex, local_path, colorspace_kind, alpha_mode="STRAIGHT", source=""):
        ident = _ident_str(tex)
        cs = compat.colorspace(colorspace_kind)
        key = (ident, cs, alpha_mode)
        img = self.by_key.get(key)
        if img is not None:
            try:
                img.name  # noqa: B018  (ReferenceError if it got deleted)
                return img, False
            except ReferenceError:
                self.by_key.pop(key, None)
        #same file loaded w a diff colorspace -> reuse the pixels via copy, rename so its obvious
        sibling = next((v for (i, c, a), v in self.by_key.items() if i == ident), None)
        try:
            if sibling is not None:
                img = sibling.copy()
                img.name = "%s (%s)" % (os.path.splitext(tex.name)[0], "data" if colorspace_kind == "Non-Color" else colorspace_kind)
            else:
                img = bpy.data.images.load(local_path, check_existing=False)
                img.name = tex.name
        except RuntimeError as exc:
            raise RuntimeError("cannot load image %s: %s" % (tex.name, exc)) from exc
        try:
            img.colorspace_settings.name = cs
        except TypeError:
            pass
        img.alpha_mode = alpha_mode
        img[IDENTITY_PROP] = ident
        if source:
            img[SOURCE_PROP] = source
        self.by_key[key] = img
        self.loaded.append(img)
        self.local_paths[img.name] = local_path
        return img, True


def finalize_storage(cache, mode, cache_root, dest_dir=None, archive_label=""):
    #what happens to textures after import
    # PACK      pack into the .blend
    # COPY      copy into dest_dir n point the images there
    # REFERENCE leave paths alone (only for loose files that stay on disk)
    #images whose file is in our temp cache r packed (or copied) whatever the mode, the cache gets cleaned
    done = 0
    problems = []
    root = os.path.normcase(os.path.abspath(cache_root)) + os.sep if cache_root else None
    #unpack targets already handed out, so 2 different body_d.png never unpack onto each other
    used = set()
    for other in bpy.data.images:
        try:
            if other.packed_file is not None:
                used.add(other.filepath_raw.replace("\\", "/").lower())
        except ReferenceError:
            continue
    for img in cache.loaded:
        try:
            if img.packed_file is not None:
                continue
        except ReferenceError:
            continue
        src = cache.local_paths.get(img.name) or bpy.path.abspath(img.filepath)
        in_cache = bool(root) and os.path.normcase(os.path.abspath(src)).startswith(root)
        try:
            if mode == "COPY" and dest_dir:
                os.makedirs(dest_dir, exist_ok=True)
                dst, fresh = copy_target(dest_dir, src)
                if fresh:
                    shutil.copy2(src, dst)
                path = dst
                if bpy.data.filepath:
                    try:
                        path = bpy.path.relpath(dst)
                    except ValueError:
                        pass  #diff drive on windows, keep absolute
                img.filepath = path
                done += 1
            elif mode == "PACK" or in_cache:
                img.pack()
                rel = (img.get(SOURCE_PROP, "") or img.name).split(":", 1)[-1].replace("\\", "/")
                #nice unpack target instead of a dead temp path
                folder = "//textures/%s/" % (archive_label or "hjt_fbx_plus")
                img.filepath_raw = _free_name(folder, rel.rsplit("/", 1)[-1], used)
                done += 1
        except (RuntimeError, OSError) as exc:
            problems.append("%s: %s" % (img.name, exc))
    return done, problems


def copy_target(dest_dir, src):
    #-> (where src goes in dest_dir, needs copying). same name + same bytes = reuse the file,
    # same name + different bytes (duplicate names from different folders, rips r full of them)
    # = name~2.png, never overwrite someone elses texture. that bug would be a nightmare to spot lol
    base = os.path.basename(src)
    stem, ext = os.path.splitext(base)
    me = os.path.normcase(os.path.abspath(src))
    n = 1
    while True:
        cand = os.path.join(dest_dir, base if n == 1 else "%s~%d%s" % (stem, n, ext))
        if os.path.normcase(os.path.abspath(cand)) == me:
            return cand, False
        if not os.path.exists(cand):
            return cand, True
        if _same_bytes(src, cand):
            return cand, False
        n += 1


def _same_bytes(a, b):
    try:
        return os.path.getsize(a) == os.path.getsize(b) and filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def _free_name(folder, base, used):
    stem, ext = os.path.splitext(base)
    n = 1
    while True:
        cand = folder + (base if n == 1 else "%s~%d%s" % (stem, n, ext))
        if cand.lower() not in used:
            used.add(cand.lower())
            return cand
        n += 1


def remove_unused(images):
    #drop images the fbx importer made that nothing uses anymore after we rebuilt materials
    n = 0
    for img in images:
        try:
            if img.users == 0:
                bpy.data.images.remove(img)
                n += 1
        except ReferenceError:
            continue
    return n
