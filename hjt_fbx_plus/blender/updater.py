# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#self updater. asks github whats the newest release, grabs the hjt_fbx_plus-x.y.z.zip asset n
# installs it over itself (extension or old school addon, whichever we r)
#
# threads do the network stuff, main thread does the bpy stuff. never touch bpy from a thread unless
#u enjoy random crashes at 3am

from __future__ import annotations

import json
import os
import re
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.request

import bpy

REPO = "Jideeh1/HJT-FBX-PLUS"
API = "https://api.github.com/repos/%s/releases" % REPO
PAGE = "https://github.com/%s/releases" % REPO
ASSET_RE = re.compile(r"(?i)^hjt[_\-]?fbx[_\-]?plus.*\.zip$")
UA = "HJT-FBX-Plus-Updater"
TIMEOUT = 15


class _Status:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = "IDLE"   # IDLE CHECKING UP_TO_DATE AVAILABLE DOWNLOADING READY INSTALLED ERROR
        self.latest = ""
        self.tag = ""
        self.notes = ""
        self.published = ""
        self.asset_url = ""
        self.asset_name = ""
        self.asset_size = 0
        self.progress = 0.0
        self.error = ""
        self.zip_path = ""
        self.checked_at = 0.0

    def set(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)
        _poke()


STATUS = _Status()
_THREAD = None


def _poke():
    #ask the main thread to redraw. timers r the only thread safe door into blender
    try:
        bpy.app.timers.register(_redraw, first_interval=0.0)
    except Exception:  # noqa: BLE001
        pass


def _redraw():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is not None:
        for win in wm.windows:
            if win.screen is None:
                continue
            for area in win.screen.areas:
                if area.type in ("VIEW_3D", "PREFERENCES"):
                    area.tag_redraw()
    return None


def parse_version(text):
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3])) if nums else (0, 0, 0)


def current_version():
    from .. import state
    return state.addon_version() or "0.0.0"


def online_allowed():
    #4.2+ has the global "allow online access" switch, we respect it like a good citizen
    return bool(getattr(bpy.app, "online_access", True))


def _ctx():
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:  # noqa: BLE001
        pass
    return ctx


def _get(url, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    return urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx())


def _state_file():
    from .. import state
    st = state.get()
    base = st.user_dir if st is not None else tempfile.gettempdir()
    return os.path.join(base, "updater.json")


def load_last_check():
    try:
        with open(_state_file(), "r", encoding="utf-8") as fh:
            d = json.load(fh)
        STATUS.checked_at = float(d.get("checked_at", 0.0))
        if d.get("latest") and parse_version(d["latest"]) > parse_version(current_version()):
            STATUS.set(state="AVAILABLE", latest=d["latest"], tag=d.get("tag", ""), notes=d.get("notes", ""),
                       asset_url=d.get("asset_url", ""), asset_name=d.get("asset_name", ""),
                       asset_size=int(d.get("asset_size", 0)), published=d.get("published", ""))
    except (OSError, ValueError, TypeError):
        pass


def _save_check():
    try:
        with open(_state_file(), "w", encoding="utf-8") as fh:
            json.dump({"checked_at": STATUS.checked_at, "latest": STATUS.latest, "tag": STATUS.tag,
                       "notes": STATUS.notes[:4000], "asset_url": STATUS.asset_url, "asset_name": STATUS.asset_name,
                       "asset_size": STATUS.asset_size, "published": STATUS.published}, fh)
    except OSError:
        pass


def _pick_release(releases, prerelease):
    best = None
    for r in releases:
        if r.get("draft") or (r.get("prerelease") and not prerelease):
            continue
        assets = [a for a in r.get("assets", []) if a.get("name", "").lower().endswith(".zip")]
        asset = next((a for a in assets if ASSET_RE.match(a.get("name", ""))), assets[0] if assets else None)
        if asset is None:
            continue
        v = parse_version(r.get("tag_name") or r.get("name"))
        if best is None or v > best[0]:
            best = (v, r, asset)
    return best


def _check_worker(prerelease):
    try:
        #every release, highest version wins. /latest only knows the one github flagged "Latest",
        # so a release published w/o that flag (or a hotfix for an older line) was invisible
        with _get(API + "?per_page=100") as resp:
            releases = json.loads(resp.read().decode("utf-8"))
        best = _pick_release(releases, prerelease)
        now = time.time()
        if best is None:
            STATUS.set(state="ERROR", error="No release with a .zip asset found on GitHub", checked_at=now)
            return
        v, rel, asset = best
        latest = ".".join(str(x) for x in v)
        newer = v > parse_version(current_version())
        STATUS.set(state="AVAILABLE" if newer else "UP_TO_DATE", latest=latest, tag=rel.get("tag_name", ""),
                   notes=(rel.get("body") or "").strip(), published=(rel.get("published_at") or "")[:10],
                   asset_url=asset.get("browser_download_url", ""), asset_name=asset.get("name", ""),
                   asset_size=int(asset.get("size", 0)), error="", checked_at=now)
        _save_check()
    except urllib.error.HTTPError as exc:
        msg = "GitHub said %d %s" % (exc.code, exc.reason)
        if exc.code == 403:
            msg += " (rate limited, try again in a bit)"
        elif exc.code == 404:
            msg += " (no published release yet)"
        STATUS.set(state="ERROR", error=msg, checked_at=time.time())
    except Exception as exc:  # noqa: BLE001
        STATUS.set(state="ERROR", error="Could not reach GitHub: %s" % exc, checked_at=time.time())


def check(prerelease=False):
    global _THREAD
    if not online_allowed():
        STATUS.set(state="ERROR", error="Online access is disabled (Preferences > System > Network)")
        return False
    if _THREAD is not None and _THREAD.is_alive():
        return False
    STATUS.set(state="CHECKING", error="")
    _THREAD = threading.Thread(target=_check_worker, args=(prerelease,), name="hjt-updater", daemon=True)
    _THREAD.start()
    return True


def _download_worker(then_install):
    url = STATUS.asset_url
    try:
        fd, path = tempfile.mkstemp(prefix="hjt_fbx_plus_update_", suffix=".zip")
        os.close(fd)
        with _get(url, accept="application/octet-stream") as resp, open(path, "wb") as out:
            total = int(resp.headers.get("Content-Length") or STATUS.asset_size or 0)
            done = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total:
                    STATUS.progress = min(1.0, done / float(total))
                    _poke()
        import zipfile
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if not any(n.endswith("blender_manifest.toml") or n.endswith("__init__.py") for n in names):
                raise ValueError("downloaded zip doesnt look like the add-on")
            bad = zf.testzip()
            if bad:
                raise ValueError("corrupt file in zip: %s" % bad)
        STATUS.set(state="READY", zip_path=path, progress=1.0)
        if then_install:
            bpy.app.timers.register(_install_from_timer, first_interval=0.1)
    except Exception as exc:  # noqa: BLE001
        STATUS.set(state="ERROR", error="Download failed: %s" % exc, progress=0.0)


def download(then_install=False):
    global _THREAD
    if not STATUS.asset_url:
        return False
    if _THREAD is not None and _THREAD.is_alive():
        return False
    STATUS.set(state="DOWNLOADING", progress=0.0, error="")
    _THREAD = threading.Thread(target=_download_worker, args=(then_install,), name="hjt-updater-dl", daemon=True)
    _THREAD.start()
    return True


def _install_from_timer():
    wm = bpy.context.window_manager
    win = wm.windows[0] if wm and wm.windows else None
    try:
        if win is not None:
            with bpy.context.temp_override(window=win):
                install(STATUS.zip_path)
        else:
            install(STATUS.zip_path)
    except Exception as exc:  # noqa: BLE001
        STATUS.set(state="ERROR", error="Install failed: %s" % exc)
    return None


def install(zip_path):
    #main thread only. replaces ourselves. blender handles disable -> swap files -> enable
    # for extensions, the old addon way needs the zip unpacked over the folder
    from .. import state
    pkg = state.PACKAGE
    if not zip_path or not os.path.isfile(zip_path):
        raise RuntimeError("update zip is missing, download it again")
    if pkg.startswith("bl_ext."):
        repo = pkg.split(".")[1]
        res = bpy.ops.extensions.package_install_files(filepath=zip_path, repo=repo, enable_on_install=True, overwrite=True)
    else:
        res = bpy.ops.preferences.addon_install(filepath=zip_path, overwrite=True)
        if "FINISHED" in res:
            import addon_utils
            addon_utils.enable(pkg, default_set=True, persistent=True)
    if "FINISHED" not in res:
        raise RuntimeError("Blender refused the package (%s)" % ", ".join(res))
    STATUS.set(state="INSTALLED", error="")
    try:
        os.remove(zip_path)
    except OSError:
        pass
    return True


def auto_check(interval_days, prerelease=False):
    #called once after startup. skips if checked recently or user turned off online access
    load_last_check()
    if not online_allowed():
        return
    if time.time() - STATUS.checked_at < max(0.0, interval_days) * 86400.0:
        return
    check(prerelease)
