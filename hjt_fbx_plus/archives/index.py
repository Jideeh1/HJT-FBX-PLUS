# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#directory tree over an archive listing so the ui can browse it lazily
# archives often dont list folders explicitly so we invent them from the file paths

from __future__ import annotations


class DirNode:
    __slots__ = ("path", "name", "dirs", "files", "size", "fbx", "images")

    def __init__(self, path):
        self.path = path
        self.name = path.rsplit("/", 1)[-1] if path else ""
        self.dirs = []
        self.files = []
        self.size = 0
        self.fbx = 0
        self.images = 0


class ArchiveIndex:
    def __init__(self, entries):
        self.dirs = {"": DirNode("")}
        self.files = []
        self.fbx = []
        self.images = []
        self.data = []
        dirs = self.dirs
        for e in entries:
            if e.is_dir:
                self._ensure(e.path)
                continue
            self.files.append(e)
            parent = e.path.rpartition("/")[0]
            node = self._ensure(parent)
            node.files.append(e)
            kind = e.kind
            if kind == "fbx":
                self.fbx.append(e)
            elif kind == "image":
                self.images.append(e)
            elif kind == "data":
                self.data.append(e)
            #roll sizes n counts up the tree
            p = parent
            while True:
                d = dirs[p]
                d.size += e.size
                if kind == "fbx":
                    d.fbx += 1
                elif kind == "image":
                    d.images += 1
                if not p:
                    break
                p = p.rpartition("/")[0]
        for d in dirs.values():
            d.dirs.sort(key=str.lower)
            d.files.sort(key=lambda x: x.path.lower())
        self.fbx.sort(key=lambda x: x.path.lower())

    def _ensure(self, path):
        node = self.dirs.get(path)
        if node is not None:
            return node
        node = DirNode(path)
        self.dirs[path] = node
        parent = path.rpartition("/")[0] if "/" in path else ""
        if path:
            pnode = self._ensure(parent)
            pnode.dirs.append(path)
        return node

    def total_size(self):
        return self.dirs[""].size

    def stats(self):
        return {"files": len(self.files), "fbx": len(self.fbx), "images": len(self.images),
                "data": len(self.data), "dirs": len(self.dirs) - 1, "size": self.total_size()}
