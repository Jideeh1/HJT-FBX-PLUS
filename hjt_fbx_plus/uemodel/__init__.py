# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

from .reader import UEFormatError, UEModel, read_uemodel, summarize  # noqa: F401

EXT = ".uemodel"
ALL_PARTS = "*" + EXT  #member suffix meaning "every .uemodel in this folder, one rig"


def is_uemodel(path):
    return (path or "").lower().endswith(EXT)
