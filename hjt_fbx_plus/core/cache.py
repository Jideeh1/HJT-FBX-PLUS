# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#temp cache for stuff pulled out of archives
# blenders fbx importer wants a real file on disk so we extract ONLY what an import needs
# (the fbx + the textures it uses) into a per session folder, n clean it up after
#
# layout:  <root>/session-<pid>-<id>/<archive name>_<hash>/<original relative paths>
# keeping the relative paths means "Textures/body.png" refs inside the fbx still resolve

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import threading
import time
import uuid

POLICIES = ("AFTER_IMPORT",)  #extracted models never outlive their import


def default_root():
    return os.path.join(tempfile.gettempdir(), "hjt_fbx_plus_cache")


def _archive_key(path):
    try:
        st = os.stat(path)
        sig = "%s|%d|%d" % (os.path.abspath(path), st.st_size, int(st.st_mtime))
    except OSError:
        sig = os.path.abspath(path)
    return hashlib.sha1(sig.encode("utf-8", "surrogateescape")).hexdigest()[:12]


class CacheManager:
    def __init__(self, root=None, policy="AFTER_IMPORT", max_mb=0):
        self.root = os.path.abspath(root or default_root())
        self.policy = policy if policy in POLICIES else "AFTER_IMPORT"
        self.max_bytes = int(max_mb) * 1024 * 1024
        self.session = os.path.join(self.root, "session-%d-%s" % (os.getpid(), uuid.uuid4().hex[:8]))
        self._locks = {}
        self._manifests = {}
        self._glock = threading.Lock()

    def configure(self, root=None, policy=None, max_mb=None):
        if root and os.path.abspath(root) != self.root:
            #new location only applies to new sessions, old files get cleaned normally
            self.cleanup_session()
            self.root = os.path.abspath(root)
            self.session = os.path.join(self.root, "session-%d-%s" % (os.getpid(), uuid.uuid4().hex[:8]))
            self._manifests.clear()
        if policy in POLICIES:
            self.policy = policy
        if max_mb is not None:
            self.max_bytes = int(max_mb) * 1024 * 1024

    def _lock_for(self, key):
        with self._glock:
            lk = self._locks.get(key)
            if lk is None:
                lk = self._locks[key] = threading.Lock()
            return lk

    def archive_dir(self, archive_path):
        stem = os.path.basename(archive_path)
        for ch in " .()[]{}":
            stem = stem.replace(ch, "_")
        return os.path.join(self.session, "%s_%s" % (stem[:40], _archive_key(archive_path)))

    def loose_dir(self, name="loose"):
        #for prepared copies of normal (non archive) fbx files
        d = os.path.join(self.session, name)
        os.makedirs(d, exist_ok=True)
        return d

    def materialize(self, reader, entries, progress=None, cancel=None):
        #make sure entries exist on disk, reuse anything already extracted. -> {entry.path: local}
        key = _archive_key(reader.path)
        dest = self.archive_dir(reader.path)
        if reader.password is None and any(e.encrypted for e in entries):
            #even if a decrypted copy sits in the cache from earlier: no password, no file. consistent > clever
            from ..archives.base import PasswordRequired
            raise PasswordRequired("'%s' is password protected" % os.path.basename(reader.path))
        with self._lock_for(key):
            manifest = self._manifests.setdefault(key, {})
            todo = []
            out = {}
            for e in entries:
                if e.is_dir:
                    continue
                local = manifest.get(e.path)
                if local and os.path.isfile(local) and os.path.getsize(local) == e.size:
                    out[e.path] = local
                else:
                    todo.append(e)
            if todo:
                if self.max_bytes:
                    self.enforce_limit(sum(e.size for e in todo))
                got = reader.extract(todo, dest, progress=progress, cancel=cancel)
                manifest.update(got)
                out.update(got)
            return out

    def release_archive(self, archive_path):
        key = _archive_key(archive_path)
        with self._lock_for(key):
            self._manifests.pop(key, None)
            shutil.rmtree(self.archive_dir(archive_path), ignore_errors=True)

    def release_files(self, paths):
        #delete specific extracted files (used after import when policy is AFTER_IMPORT)
        n = 0
        for p in paths:
            if p and os.path.abspath(p).startswith(self.session + os.sep):
                try:
                    os.remove(p)
                    n += 1
                except OSError:
                    pass
        for man in self._manifests.values():
            for k in [k for k, v in man.items() if v in paths]:
                man.pop(k, None)
        return n

    def cleanup_session(self):
        self._manifests.clear()
        shutil.rmtree(self.session, ignore_errors=True)

    def purge_stale(self, max_age_hours=24.0):
        #leftovers from crashed sessions. only touches our own session-* folders
        if not os.path.isdir(self.root):
            return 0
        n = 0
        now = time.time()
        for name in os.listdir(self.root):
            full = os.path.join(self.root, name)
            if not name.startswith("session-") or full == self.session or not os.path.isdir(full):
                continue
            try:
                age = (now - os.path.getmtime(full)) / 3600.0
            except OSError:
                continue
            if age >= max_age_hours or not _pid_alive(name):
                shutil.rmtree(full, ignore_errors=True)
                n += 1
        return n

    def usage(self):
        total = 0
        files = 0
        if os.path.isdir(self.root):
            for root, _dirs, fs in os.walk(self.root):
                for f in fs:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                        files += 1
                    except OSError:
                        pass
        return total, files

    def clear_all(self):
        #the "clear cache" button. our session folder + every stale one
        self.cleanup_session()
        return self.purge_stale(0.0)

    def enforce_limit(self, incoming=0):
        #drop the oldest archive folders of this session until we fit
        if not self.max_bytes or not os.path.isdir(self.session):
            return
        dirs = []
        total = 0
        for name in os.listdir(self.session):
            full = os.path.join(self.session, name)
            size = 0
            for root, _d, fs in os.walk(full):
                for f in fs:
                    try:
                        size += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            total += size
            try:
                dirs.append((os.path.getmtime(full), full, size))
            except OSError:
                pass
        dirs.sort()
        while dirs and total + incoming > self.max_bytes:
            _mt, full, size = dirs.pop(0)
            shutil.rmtree(full, ignore_errors=True)
            total -= size
            for key in list(self._manifests):
                self._manifests[key] = {k: v for k, v in self._manifests[key].items() if not v.startswith(full)}


def _pid_alive(session_name):
    #session-<pid>-<id>. only checkable on posix, windows just goes by age
    try:
        pid = int(session_name.split("-")[1])
    except (IndexError, ValueError):
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
