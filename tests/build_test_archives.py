# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#packs the generated assets into every archive flavour we support (plus some broken ones)
#   python tests/build_test_archives.py <assets dir> <out dir>
# needs 7z for the encrypted/7z ones n rar for the rar one, missing tools just skip those files

import os
import shutil
import subprocess
import sys
import zipfile

PASSWORD = "hjt-test"


def run(args, cwd):
    try:
        r = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def main(assets, out):
    os.makedirs(out, exist_ok=True)
    made = []
    z = os.path.join(out, "assets.zip")
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _d, files in os.walk(assets):
            for f in files:
                full = os.path.join(root, f)
                zf.write(full, os.path.relpath(full, assets))
    made.append(z)
    seven = shutil.which("7zz") or shutil.which("7z") or shutil.which("7za")
    if seven:
        for name, args in (("assets_aes.zip", ["-tzip", "-mem=AES256", "-p" + PASSWORD]),
                           ("assets.7z", ["-t7z", "-m0=lzma2", "-ms=on"]),
                           ("assets_enc.7z", ["-t7z", "-m0=lzma2", "-mhe=on", "-p" + PASSWORD])):
            dst = os.path.join(out, name)
            if os.path.exists(dst):
                os.remove(dst)
            if run([seven, "a", "-bso0", "-bsp0"] + args + [dst, "."], assets):
                made.append(dst)
    rar = shutil.which("rar")
    if rar:
        dst = os.path.join(out, "assets.rar")
        if os.path.exists(dst):
            os.remove(dst)
        if run([rar, "a", "-idq", "-r", "-ma5", dst, "."], assets):
            made.append(dst)
    #broken ones: cut in half
    for src, dst in ((z, "broken.zip"), (os.path.join(out, "assets.7z"), "broken.7z")):
        if os.path.exists(src):
            data = open(src, "rb").read()
            with open(os.path.join(out, dst), "wb") as fh:
                fh.write(data[: len(data) // 2])
            made.append(os.path.join(out, dst))
    for m in made:
        print("built", os.path.basename(m), os.path.getsize(m))


if __name__ == "__main__":
    main(os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2]))
