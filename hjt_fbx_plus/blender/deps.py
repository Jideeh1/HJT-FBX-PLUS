# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#optional python packages. NOTHING here is required, they just make some things faster/possible:
#  pycryptodomex  ~400 mb/s aes instead of ~20 mb/s for encrypted 7z / zip
#  py7zr          7z archives w ppmd / bcj2 / zstd when no 7-zip is installed
#installed into a private folder w blenders own pip, never into blenders site-packages

from __future__ import annotations

import importlib
import os
import subprocess
import sys

OPTIONAL = {
    "pycryptodomex": {"module": "Cryptodome", "label": "Fast AES (pycryptodomex)",
                      "why": "Much faster decryption of password-protected 7z and AES zip archives"},
    "py7zr": {"module": "py7zr", "label": "py7zr", "why": "Opens 7z archives using PPMd, BCJ2 or Zstandard when 7-Zip is not installed"},
}


def lib_dir(user_dir):
    d = os.path.join(user_dir, "lib", "py%d%d" % sys.version_info[:2])
    return d


def activate(user_dir):
    d = lib_dir(user_dir)
    if os.path.isdir(d) and d not in sys.path:
        sys.path.append(d)  #append, never shadow blenders own modules
    return d


def status():
    out = {}
    for pkg, info in OPTIONAL.items():
        try:
            mod = importlib.import_module(info["module"])
            out[pkg] = getattr(mod, "__version__", "installed")
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001  broken compiled packages can throw non-Exception stuff
            out[pkg] = ""
    return out


def install(task, package, user_dir):
    #worker thread. pip into our own folder, ensurepip first if this blender has no pip
    if package not in OPTIONAL:
        raise ValueError("unknown package %s" % package)
    target = lib_dir(user_dir)
    os.makedirs(target, exist_ok=True)
    py = sys.executable
    kw = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "timeout": 600}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000
    probe = subprocess.run([py, "-m", "pip", "--version"], **kw)
    if probe.returncode != 0:
        task.set_progress(0.2, "Setting up pip")
        subprocess.run([py, "-m", "ensurepip", "--user"], **kw)
    task.set_progress(0.4, "Installing %s" % package)
    p = subprocess.run([py, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--upgrade",
                        "--target", target, package], **kw)
    text = p.stdout.decode("utf-8", "replace")
    if p.returncode != 0:
        raise RuntimeError("pip failed (%d): %s" % (p.returncode, text.strip().splitlines()[-1] if text.strip() else "no output"))
    if target not in sys.path:
        sys.path.append(target)
    importlib.invalidate_caches()
    return package
