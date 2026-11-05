# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#loads built-in profiles (read only, shipped w the addon) + user profiles (a folder the user owns)
# user profiles w the same id as a builtin win, thats how "edit a builtin" works (copy then edit)

from __future__ import annotations

import json
import os
import re

from .schema import Profile, validate

BUILTIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "builtin")


class ProfileError(Exception):
    pass


class Registry:
    def __init__(self, user_dir=None):
        self.user_dir = user_dir
        self.profiles = {}
        self.errors = []
        self.reload()

    def reload(self):
        self.profiles = {}
        self.errors = []
        self._load_dir(BUILTIN_DIR, builtin=True)
        if self.user_dir and os.path.isdir(self.user_dir):
            self._load_dir(self.user_dir, builtin=False)

    def _load_dir(self, d, builtin):
        for fn in sorted(os.listdir(d)):
            if not fn.lower().endswith(".json"):
                continue
            path = os.path.join(d, fn)
            try:
                with open(path, "r", encoding="utf-8-sig") as fh:
                    data = json.load(fh)
            except (OSError, ValueError) as exc:
                self.errors.append("%s: %s" % (fn, exc))
                continue
            problems = validate(data)
            if problems:
                self.errors.append("%s: %s" % (fn, "; ".join(problems[:3])))
                if builtin:
                    continue
                #user profile w small problems still loads, broken bits r skipped by Profile
            try:
                self.profiles[data["id"]] = Profile(data, path, builtin)
            except Exception as exc:  # noqa: BLE001
                self.errors.append("%s: %s" % (fn, exc))

    def get(self, pid):
        return self.profiles.get(pid) or self.profiles.get("generic")

    def ordered(self):
        #generic first, then builtins, then user ones, alphabetical inside each group
        items = list(self.profiles.values())
        items.sort(key=lambda p: (p.id != "generic", not p.builtin, p.name.lower()))
        return items

    #user side
    def _user_path(self, pid):
        if not self.user_dir:
            raise ProfileError("no user profile folder configured")
        os.makedirs(self.user_dir, exist_ok=True)
        return os.path.join(self.user_dir, "%s.json" % pid)

    def save(self, data):
        problems = validate(data)
        if problems:
            raise ProfileError("; ".join(problems))
        path = self._user_path(data["id"])
        tmp = path + ".part"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
        self.profiles[data["id"]] = Profile(data, path, False)
        return path

    def unique_id(self, base):
        base = re.sub(r"[^A-Za-z0-9_\-]+", "_", base).strip("_")[:48] or "profile"
        pid = base
        n = 2
        while pid in self.profiles:
            pid = "%s_%d" % (base, n)
            n += 1
        return pid

    def duplicate(self, pid, new_name=None):
        src = self.get(pid)
        data = src.to_json()
        data["id"] = self.unique_id((src.id + "_custom") if src.builtin else (src.id + "_copy"))
        data["name"] = new_name or ("%s (Custom)" % src.name)
        data["author"] = data.get("author") or ""
        self.save(data)
        return data["id"]

    def create(self, name):
        data = {"schema": 1, "id": self.unique_id(name.lower()), "name": name, "description": "", "author": "",
                "version": "1.0"}
        self.save(data)
        return data["id"]

    def delete(self, pid):
        p = self.profiles.get(pid)
        if p is None or p.builtin:
            raise ProfileError("built-in profiles cannot be deleted")
        try:
            os.remove(p.path)
        except OSError as exc:
            raise ProfileError(str(exc)) from exc
        self.reload()

    def export(self, pid, path):
        p = self.get(pid)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(p.to_json(), fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        return path

    def import_file(self, path, overwrite=False):
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ProfileError("cannot read %s: %s" % (os.path.basename(path), exc)) from exc
        problems = validate(data)
        if problems:
            raise ProfileError("; ".join(problems[:5]))
        existing = self.profiles.get(data["id"])
        if existing is not None and (existing.builtin or not overwrite):
            data["id"] = self.unique_id(data["id"])
        self.save(data)
        return data["id"]
